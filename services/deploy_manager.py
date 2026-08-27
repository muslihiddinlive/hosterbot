"""
services/deploy_manager.py

Har bir foydalanuvchi botini alohida subprocess sifatida ishga tushiradi.

XAVFSIZLIK HAQIDA MUHIM ESLATMA:
Bu yerdagi isolation "yengil" darajada — RLIMIT orqali RAM/CPU chegaralanadi,
lekin bu to'liq sandbox EMAS. Fayl tizimi va tarmoqqa kirish hali ham cheklanmagan.
Faqat MAX_BOTS_PER_USER kichik (masalan, 3) va foydalanuvchilar administratsiya
tomonidan tekshirilgan/ishonchli bo'lgan holatlar uchun mos.
Agar keyinchalik foydalanuvchilar soni oshsa — Docker/Firecracker asosidagi
to'liq isolation'ga o'tish tavsiya etiladi.
"""
import os
import resource
import subprocess
import signal
import sys
import threading

from config import BOT_MEMORY_LIMIT_MB, BOT_CPU_TIME_LIMIT_SEC
from services.deploy_log import write_stage

# Xavfli bo'lishi mumkin bo'lgan importlar (statik tekshiruv — to'liq himoya emas,
# faqat birinchi qatlam sifatida)
BLOCKED_PATTERNS = [
    "os.system(",
    "subprocess.Popen(\"rm",
    "subprocess.Popen('rm",
    "shutil.rmtree(\"/",
    "shutil.rmtree('/",
    "eval(input(",
    "exec(input(",
    "os.remove(\"/",
    "os.remove('/",
    "socket.socket(",
    "__import__(\"os\")",
    "ctypes.CDLL(",
    "os.fork(",
    "os.setuid(",
    "/etc/passwd",
    "base64.b64decode(",
]

# bot_id -> subprocess.Popen
_running_processes: dict[int, subprocess.Popen] = {}


def static_scan(code_text: str) -> list[str]:
    """Oddiy statik tekshiruv — shubhali qatorlarni qaytaradi (blokламайди, faqat ogohlantiradi)."""
    warnings = []
    for pattern in BLOCKED_PATTERNS:
        if pattern in code_text:
            warnings.append(pattern)
    return warnings


def _limit_resources():
    """
    subprocess ichida chaqiriladi (preexec_fn). Faqat RAM emas, endi CPU vaqti, ochiq fayl
    deskriptorlar soni va yaratilishi mumkin bo'lgan fayl hajmini ham cheklaydi — bittasi
    ("noqonuniy" yoki xato) bot butun serverni (disk to'lib ketishi, fd tugashi orqali)
    ag'darib yubormasligi uchun. DIQQAT: bu hamon to'liq sandbox emas — tarmoq va mavjud
    fayllarni o'qish/o'chirish hali ham cheklanmagan (Docker/Firecracker'gacha bo'lgan oraliq chora).
    """
    mem_bytes = BOT_MEMORY_LIMIT_MB * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    except (ValueError, resource.error):
        pass  # ba'zi platformalarda RLIMIT_AS cheklanmagan bo'lishi mumkin

    if BOT_CPU_TIME_LIMIT_SEC > 0:
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (BOT_CPU_TIME_LIMIT_SEC, BOT_CPU_TIME_LIMIT_SEC))
        except (ValueError, resource.error):
            pass

    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    except (ValueError, resource.error):
        pass

    try:
        # Bitta botga 500MB dan ko'p disk yozishga ruxsat bermaymiz (disk to'lib qolishning oldini olish)
        five_hundred_mb = 500 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (five_hundred_mb, five_hundred_mb))
    except (ValueError, resource.error):
        pass


def install_requirements(workdir: str, requirements_path: str, log_file) -> bool:
    # DIQQAT: bare "pip" ba'zi muhitlarda PATH'da bo'lmasligi mumkin (masalan faqat
    # python3/pip3 bor). sys.executable -m pip AYNAN shu jarayonni ishga tushirgan
    # python'ning pip'ini ishlatadi — ancha ishonchli.
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--break-system-packages", "-r", requirements_path],
        cwd=workdir, stdout=log_file, stderr=subprocess.STDOUT, timeout=300,
    )
    return result.returncode == 0


def normalize_interpreter(cmd: str) -> str:
    """
    Foydalanuvchi yoki avtomatik generatsiya "python ..." / "pip ..." deb yozgan bo'lsa,
    buni AYNAN shu platformani ishga tushirgan interpreterga (sys.executable) moslashtiradi.
    Sabab: ba'zi hosting muhitlarida (masalan ba'zi Render konfiguratsiyalari) PATH'da
    bare "python"/"pip" umuman bo'lmaydi (faqat python3/pip3, yoki alohida venv) — shu sabab
    build/start bosqichi "command not found" bilan har doim muvaffaqiyatsiz bo'lib qolishi mumkin edi.
    Faqat qator BOSHIDAGI so'zni almashtiramiz, ichkarida (masalan argumentda) "python" so'zi
    bo'lsa tegilmaydi.
    """
    stripped = cmd.strip()
    if stripped.startswith("pip install") or stripped == "pip" or stripped.startswith("pip "):
        rest = stripped[len("pip"):]
        return f'"{sys.executable}" -m pip{rest}'
    if stripped.startswith("python ") or stripped == "python":
        rest = stripped[len("python"):]
        return f'"{sys.executable}"{rest}'
    if stripped.startswith("python3 ") or stripped == "python3":
        rest = stripped[len("python3"):]
        return f'"{sys.executable}"{rest}'
    # "py" — Windows'dagi python launcher qisqartmasi (masalan "py bot.py").
    # Ko'p foydalanuvchi shunday yozadi, lekin Linux serverda "py" buyrug'i
    # mavjud emas ("py: not found", exit 127) — shu sabab uni ham
    # sys.executable'ga moslashtiramiz. "python"/"python3" bilan chalkashmasligi
    # uchun aniq "py " yoki yakka "py" ga tekshiramiz.
    if stripped.startswith("py ") or stripped == "py":
        rest = stripped[len("py"):]
        return f'"{sys.executable}"{rest}'
    return cmd


def run_build_command(workdir: str, build_cmd: str, log_file) -> bool:
    if not build_cmd:
        write_stage(log_file, "build_skipped", reason="build_cmd bo'sh")
        return True
    build_cmd = normalize_interpreter(build_cmd)
    write_stage(log_file, "build_start", cmd=build_cmd)
    try:
        result = subprocess.run(
            build_cmd, shell=True, cwd=workdir, stdout=log_file, stderr=subprocess.STDOUT, timeout=300,
        )
    except subprocess.TimeoutExpired:
        write_stage(log_file, "build_timeout", limit_sec=300)
        return False
    write_stage(log_file, "build_ok" if result.returncode == 0 else "build_failed", exit_code=result.returncode)
    return result.returncode == 0


def start_bot_process(bot_id: int, workdir: str, start_cmd: str, env_pairs: dict) -> int:
    """Botni subprocess sifatida ishga tushiradi, PID qaytaradi."""
    log_path = os.path.join(workdir, "run.log")
    log_file = open(log_path, "a", encoding="utf-8")

    # DIQQAT (xavfsizlik): oldin os.environ.copy() ishlatilgan edi — bu HosterBot
    # PLATFORMASINING o'z maxfiy o'zgaruvchilarini (masalan uning O'Z BOT_TOKEN'i,
    # STORAGE_GROUP_ID, DB yo'llari) HAR BIR foydalanuvchi botiga bericha meros
    # qilib berardi. Amalda buning oqibati ham chiqdi: agar foydalanuvchi o'z
    # BOT_TOKEN'ini noto'g'ri kiritsa (masalan kalitni yozmay, faqat qiymatni
    # yuborsa), bot xato bilan to'xtash o'rniga JIMGINA platformaning O'Z tokeni
    # bilan ishga tushib ketardi — bu ham chalkash xato, ham xavfsizlik teshigi
    # (foydalanuvchi bot kodi orqali platforma sirlarini oshkor qilishi mumkin edi).
    # Shu sabab endi faqat zararsiz, umumiy tizim o'zgaruvchilari + foydalanuvchi
    # aniq bergan ENV qiymatlari beriladi.
    SAFE_ENV_KEYS = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "PYTHONIOENCODING", "TMPDIR"}
    full_env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}
    full_env.update(env_pairs)

    start_cmd = normalize_interpreter(start_cmd)
    write_stage(log_file, "run_start", cmd=start_cmd, bot_id=bot_id)

    proc = subprocess.Popen(
        start_cmd,
        shell=True,
        cwd=workdir,
        env=full_env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        preexec_fn=_limit_resources,
        start_new_session=True,  # o'z process guruhi yetakchisi bo'ladi (bir vaqtning o'zida
        # setsid ham chaqiriladi) — shell "python ..." ni exec bilan almashtirmay, bola process
        # sifatida qoldirib ketsa ham, to'xtatishda BUTUN guruhni birga o'chirish imkonini beradi.
        # Aks holda faqat shell processga SIGTERM yuborilsa, ichidagi python "yetim" bo'lib qolib,
        # eski token bilan ishlashda davom etib ketishi mumkin edi.
    )
    _running_processes[bot_id] = proc

    def _watch():
        proc.wait()
        # log_file yopilishidan oldin exit_code'ni yozib qo'yamiz — bot nega to'xtaganini
        # (0 = toza chiqish, boshqa = crash) keyinchalik run.log'dan bir qarashda ko'rish uchun.
        try:
            write_stage(log_file, "run_exit", exit_code=proc.returncode, bot_id=bot_id)
            log_file.close()
        except (ValueError, OSError):
            pass  # log_file allaqachon yopilgan bo'lishi mumkin
        _running_processes.pop(bot_id, None)

    threading.Thread(target=_watch, daemon=True).start()
    return proc.pid


def stop_bot_process(bot_id: int) -> bool:
    proc = _running_processes.get(bot_id)
    if proc is None:
        return False
    try:
        # Faqat shell processga emas, BUTUN process guruhiga (shell + uning ichidagi
        # python) signal yuboramiz — start_new_session=True bilan ishga tushirilgani
        # uchun bu xavfsiz. Aks holda ba'zi shell'larda ichkaridagi python "yetim"
        # bo'lib qolib, to'xtatilgandan/o'chirilgandan keyin ham eski token bilan
        # fonda ishlashda davom etib ketishi mumkin edi.
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
    except (ProcessLookupError, OSError):
        pass
    _running_processes.pop(bot_id, None)
    return True


def is_running(bot_id: int) -> bool:
    proc = _running_processes.get(bot_id)
    if proc is None:
        return False
    return proc.poll() is None


def list_running_pids() -> dict[int, int]:
    """Hozir tirik deb hisoblangan (proc.poll() is None) barcha bot_id -> pid
    juftlarini qaytaradi. resource_monitor bu orqali RAM sarfini yig'adi —
    boshqa modullar _running_processes'ga to'g'ridan-to'g'ri tegmasligi uchun."""
    return {bot_id: proc.pid for bot_id, proc in _running_processes.items() if proc.poll() is None}


def read_log_tail(workdir: str, n_lines: int = 60) -> str:
    log_path = os.path.join(workdir, "run.log")
    if not os.path.exists(log_path):
        return "Log topilmadi (bot hali ishga tushirilmagan)."
    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    return "".join(lines[-n_lines:]) or "Log bo'sh."
