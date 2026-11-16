"""
울산교육연수원 로그인 봇 (1단계: 로그인)

동작 순서
  1) 봇 전용 크롬 프로필로 크롬을 띄운다 (평소 쓰는 크롬과 분리)
  2) LOGIN_URL 로 이동한다
  3) 아이디/비밀번호 입력칸을 찾는다
     - .env 에 셀렉터를 적어두면 그것을 쓰고,
     - 없으면 페이지에서 비밀번호 칸과 그 앞의 아이디 칸을 자동으로 찾는다
  4) 값을 입력하고 로그인 버튼을 누른다 (버튼이 없으면 Enter)
  5) 로그인 성공 여부를 확인한다

준비
  python -m pip install selenium
  .env.example 을 .env 로 복사한 뒤 값 채우기
"""

import getpass
import os
import subprocess
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE_DIR = Path(__file__).resolve().parent
# exe 로 만들었을 때는 실행 파일 옆이 아니라 사용자 폴더(%LOCALAPPDATA%\UetiBot)에 데이터를 둔다
FROZEN = getattr(sys, "frozen", False)
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "UetiBot" if FROZEN else BASE_DIR


def log(*args) -> None:
    """화면 출력 함수. GUI 프로그램은 이 함수를 바꿔 끼워 로그 창에 표시한다."""
    print(*args, flush=True)


def err_text(e: Exception) -> str:
    """셀레니움 TimeoutException 은 메시지가 비어 있어 무엇이 실패했는지 알기 어려우므로 보완"""
    if isinstance(e, TimeoutException) and not (e.msg or "").strip():
        return f"{TIMEOUT}초 안에 기다리던 화면/요소가 나타나지 않았습니다 (시간 초과)"
    text = (getattr(e, "msg", None) or str(e)).strip()
    return text.splitlines()[0] if text else type(e).__name__


# ---------------------------------------------------------------------------
# 설정 읽기 (.env 파일 → 환경변수). 외부 라이브러리 없이 간단히 파싱
# ---------------------------------------------------------------------------
def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env(BASE_DIR / ".env")

LOGIN_URL = os.environ.get("LOGIN_URL", "") or "https://edu.ueti.or.kr/main.do"
USER_ID = os.environ.get("BOT_USER_ID", "")
USER_PW = os.environ.get("BOT_USER_PW", "")

# 선택: 자동 탐지가 안 될 때 CSS 셀렉터를 직접 지정
ID_SELECTOR = os.environ.get("ID_SELECTOR", "")
PW_SELECTOR = os.environ.get("PW_SELECTOR", "")
SUBMIT_SELECTOR = os.environ.get("SUBMIT_SELECTOR", "")

# 로그인 성공 후 페이지에 보이는 문구 (예: 로그아웃)
SUCCESS_TEXT = os.environ.get("SUCCESS_TEXT", "로그아웃")

PROFILE_DIR = DATA_DIR / "chrome-profile"
TIMEOUT = 15


# ---------------------------------------------------------------------------
# 크롬 실행
# ---------------------------------------------------------------------------
def close_leftover_bot_chrome() -> None:
    """지난 실행 때 열어둔 봇 크롬 창이 남아 있으면 프로필이 잠겨 새 크롬이 뜨지 못한다.
    봇 전용 프로필(chrome-profile)을 쓰는 크롬만 골라서 닫는다. 평소 쓰는 크롬은 건드리지 않음."""
    if os.name != "nt":
        return
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        f"Where-Object {{ $_.CommandLine -like '*{PROFILE_DIR}*' }} | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $_.ProcessId }"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # exe 실행 시 검은 창이 번쩍이지 않게
    )
    if result.stdout.strip():
        log("  · 지난번에 열어둔 봇 크롬 창을 닫았습니다.")
        time.sleep(1)


def open_browser() -> webdriver.Chrome:
    close_leftover_bot_chrome()
    options = webdriver.ChromeOptions()
    # 봇 전용 프로필: 쿠키가 남아서 다음 실행 때 로그인 상태가 유지될 수 있음
    options.add_argument(f"--user-data-dir={PROFILE_DIR}")
    options.add_argument("--start-maximized")
    # 스크립트가 끝나도 창을 닫지 않음 (결과를 눈으로 확인하기 위해)
    options.add_experimental_option("detach", True)
    # 봇 전용 프로필에서는 크롬 비밀번호 저장/유출 경고 팝업을 끔
    # (이 팝업이 화면을 가려 이어보기 클릭 뒤 강의실 창이 열리지 않는 문제가 있었음)
    options.add_experimental_option("prefs", {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.password_manager_leak_detection": False,
    })
    options.add_argument("--disable-features=PasswordLeakDetection,PasswordCheck")
    # Selenium 4.6+ 는 크롬 버전에 맞는 chromedriver 를 자동으로 준비함
    return webdriver.Chrome(options=options)


# ---------------------------------------------------------------------------
# 입력칸 찾기
# ---------------------------------------------------------------------------
def is_usable(el) -> bool:
    return el.is_displayed() and el.is_enabled()


def switch_to_frame_with_password(driver) -> bool:
    """로그인 폼이 iframe 안에 있는 사이트가 많아서, 비밀번호 칸이 있는 프레임으로 들어간다."""
    driver.switch_to.default_content()
    if driver.find_elements(By.CSS_SELECTOR, "input[type=password]"):
        return True
    for frame in driver.find_elements(By.CSS_SELECTOR, "iframe, frame"):
        driver.switch_to.default_content()
        driver.switch_to.frame(frame)
        if driver.find_elements(By.CSS_SELECTOR, "input[type=password]"):
            log("  · 로그인 폼이 iframe 안에 있어 해당 프레임으로 전환했습니다.")
            return True
    driver.switch_to.default_content()
    return False


def find_login_fields(driver):
    wait = WebDriverWait(driver, TIMEOUT)

    if ID_SELECTOR and PW_SELECTOR:
        id_box = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, ID_SELECTOR)))
        pw_box = driver.find_element(By.CSS_SELECTOR, PW_SELECTOR)
        return id_box, pw_box

    # 자동 탐지: 화면에 보이는 비밀번호 칸 → 같은 폼(또는 문서)에서 그 앞의 텍스트 칸
    try:
        wait.until(lambda d: switch_to_frame_with_password(d))
    except TimeoutException:
        raise RuntimeError("비밀번호 입력칸을 찾지 못했습니다. 로그인 페이지가 맞는지 확인하세요.")

    pw_candidates = [e for e in driver.find_elements(By.CSS_SELECTOR, "input[type=password]") if is_usable(e)]
    if not pw_candidates:
        raise RuntimeError("보이는 비밀번호 입력칸이 없습니다. 로그인 버튼/탭을 먼저 눌러야 하는 페이지일 수 있습니다.")
    pw_box = pw_candidates[0]

    text_inputs = pw_box.find_elements(
        By.XPATH,
        "preceding::input[not(@type) or @type='text' or @type='email' or @type='tel']",
    )
    text_inputs = [e for e in text_inputs if is_usable(e)]
    if not text_inputs:
        raise RuntimeError("아이디 입력칸을 찾지 못했습니다. .env 에 ID_SELECTOR 를 지정해 주세요.")
    id_box = text_inputs[-1]  # 비밀번호 칸 바로 앞의 입력칸

    describe("아이디 칸", id_box)
    describe("비밀번호 칸", pw_box)
    return id_box, pw_box


def describe(label: str, el) -> None:
    attrs = {k: el.get_attribute(k) for k in ("id", "name", "placeholder")}
    shown = ", ".join(f"{k}={v}" for k, v in attrs.items() if v)
    log(f"  · {label}: <input {shown}>")


def click_submit(driver, pw_box) -> None:
    if SUBMIT_SELECTOR:
        driver.find_element(By.CSS_SELECTOR, SUBMIT_SELECTOR).click()
        return

    # 같은 form 안의 제출 버튼 → 없으면 '로그인' 글자가 있는 버튼/링크 → 없으면 Enter
    xpaths = [
        "ancestor::form[1]//*[(self::button or self::input) and (@type='submit' or @type='image')]",
        "ancestor::form[1]//button",
        "following::*[(self::button or self::a or self::input) and "
        "(contains(normalize-space(.), '로그인') or contains(@value, '로그인') or contains(@alt, '로그인'))]",
    ]
    for xp in xpaths:
        buttons = [b for b in pw_box.find_elements(By.XPATH, xp) if is_usable(b)]
        if buttons:
            safe_click(driver, buttons[0])
            return
    pw_box.send_keys(Keys.ENTER)


def safe_click(driver, el) -> None:
    """팝업 등이 버튼을 가리면 일반 클릭이 실패하므로 자바스크립트 클릭으로 대신한다."""
    try:
        el.click()
    except ElementClickInterceptedException:
        log("  · 다른 요소가 버튼을 가려서 자바스크립트로 클릭합니다.")
        driver.execute_script("arguments[0].click();", el)


# ---------------------------------------------------------------------------
# 로그인
# ---------------------------------------------------------------------------
def is_logged_in(driver) -> bool:
    """화면에 '로그아웃' 버튼/링크가 보이면 이미 로그인된 상태"""
    driver.switch_to.default_content()
    xpath = f"//*[(self::a or self::button) and contains(normalize-space(), '{SUCCESS_TEXT}')]"
    return any(e.is_displayed() for e in driver.find_elements(By.XPATH, xpath))


def ask_credentials() -> None:
    global USER_ID, USER_PW
    # .env 에 없으면 실행할 때 직접 입력받음 (비밀번호는 화면에 표시되지 않음)
    if not USER_ID:
        USER_ID = input("아이디: ").strip()
    if not USER_PW:
        USER_PW = getpass.getpass("비밀번호: ")


def login(driver) -> bool:
    log(f"1) 로그인 페이지로 이동: {LOGIN_URL}")
    driver.get(LOGIN_URL)

    # 봇 전용 프로필에 쿠키가 남아 있으면 이미 로그인되어 있을 수 있음
    time.sleep(1)
    if is_logged_in(driver):
        log("  · 이미 로그인되어 있어 로그인 단계를 건너뜁니다.")
        return True
    ask_credentials()

    log("2) 입력칸 찾는 중...")
    id_box, pw_box = find_login_fields(driver)

    log("3) 아이디/비밀번호 입력")
    id_box.clear()
    id_box.send_keys(USER_ID)
    pw_box.clear()
    pw_box.send_keys(USER_PW)
    time.sleep(0.3)  # 일부 사이트는 입력 직후 스크립트 검사를 하므로 잠깐 대기

    log("4) 로그인 버튼 클릭")
    before_url = driver.current_url
    click_submit(driver, pw_box)

    # 로그인 실패 시 alert 로 알려주는 사이트가 많음
    try:
        alert = WebDriverWait(driver, 2).until(EC.alert_is_present())
        msg = alert.text
        alert.accept()
        log(f"  ! 사이트 알림창: {msg}")
        if "실패" in msg or "일치" in msg or "확인" in msg:
            return False
    except TimeoutException:
        pass

    log("5) 로그인 결과 확인 중...")
    driver.switch_to.default_content()
    try:
        WebDriverWait(driver, TIMEOUT).until(
            lambda d: SUCCESS_TEXT in d.find_element(By.TAG_NAME, "body").text
            or (d.current_url != before_url and not d.find_elements(By.CSS_SELECTOR, "input[type=password]"))
        )
        return True
    except TimeoutException:
        return False


# ---------------------------------------------------------------------------
# 2단계: 나의학습방 > 수강과정 열기
# ---------------------------------------------------------------------------
MENU_TEXT = "나의학습방"
SUBMENU_TEXT = "수강과정"
# 메뉴를 못 찾을 때 바로 여는 주소 (사이트 메뉴의 '수강과정' 링크와 같음)
MY_COURSES_URL = "https://edu.ueti.or.kr/hp/pg/programConvert.do?menuId=3000000562"


def close_member_popup(driver) -> None:
    """로그인 직후 뜨는 회원정보 팝업(#member_info_pop01)이 상단 메뉴를 가리므로 '닫기'로 닫는다."""
    for btn in driver.find_elements(By.XPATH, "//*[@id='member_info_pop01']//button[normalize-space()='닫기']"):
        if btn.is_displayed():
            btn.click()
            log("  · 메뉴를 가리는 회원정보 팝업을 닫았습니다.")
            time.sleep(0.5)
            return


def open_my_courses(driver) -> None:
    driver.switch_to.default_content()
    wait = WebDriverWait(driver, TIMEOUT)
    close_member_popup(driver)

    # '나의학습방' 메뉴는 로그인한 상태에서만 화면에 나타남
    log(f"6) 상단 메뉴 '{MENU_TEXT}' 찾는 중...")
    menu_xpath = f"//a[normalize-space()='{MENU_TEXT}']"
    try:
        WebDriverWait(driver, 5).until(
            lambda d: any(e.is_displayed() for e in d.find_elements(By.XPATH, menu_xpath))
        )
    except TimeoutException:
        log(f"  · 메뉴가 보이지 않아 주소로 바로 이동: {MY_COURSES_URL}")
        driver.get(MY_COURSES_URL)
        finish_page_load(driver)
        return
    menu = next(e for e in driver.find_elements(By.XPATH, menu_xpath) if e.is_displayed())
    # 상단 메뉴 안의 하위 메뉴 링크 (마우스를 올려야 보이는 링크)
    submenu = menu.find_element(By.XPATH, f"./ancestor::li[1]//a[normalize-space()='{SUBMENU_TEXT}']")
    target_url = submenu.get_attribute("href")

    log(f"7) '{MENU_TEXT}'에 마우스를 올려 하위 메뉴 펼치기")
    ActionChains(driver).move_to_element(menu).perform()
    try:
        WebDriverWait(driver, 3).until(lambda d: submenu.is_displayed())
        log(f"8) '{SUBMENU_TEXT}' 클릭")
        ActionChains(driver).move_to_element(submenu).click().perform()
    except TimeoutException:
        # 메뉴가 펼쳐지지 않으면 링크 주소로 바로 이동
        log(f"8) 하위 메뉴가 펼쳐지지 않아 링크로 바로 이동: {target_url}")
        driver.get(target_url)

    finish_page_load(driver)


def finish_page_load(driver) -> None:
    # 로그인이 풀렸으면 사이트가 '로그인 후 이용할 수 있습니다' 알림창을 띄움
    try:
        alert = WebDriverWait(driver, 2).until(EC.alert_is_present())
        msg = alert.text
        alert.accept()
        raise RuntimeError(f"사이트 알림창: {msg}")
    except TimeoutException:
        pass
    WebDriverWait(driver, TIMEOUT).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    log(f"수강과정 페이지 열림: {driver.title} ({driver.current_url})")


# ---------------------------------------------------------------------------
# 3단계: 이어보기 → 강의실 팝업 창으로 전환 → 동영상 재생
# ---------------------------------------------------------------------------
# 과정이 여러 개일 때 .env 의 COURSE_NAME 에 과정명 일부를 적으면 그 과정을 고름 (비우면 첫 번째)
COURSE_NAME = os.environ.get("COURSE_NAME", "")


def start_lecture(driver) -> None:
    wait = WebDriverWait(driver, TIMEOUT)

    log("9) '이어보기' 버튼 찾는 중...")
    xpath = "//a[normalize-space()='이어보기']"
    wait.until(lambda d: any(e.is_displayed() for e in d.find_elements(By.XPATH, xpath)))
    buttons = [e for e in driver.find_elements(By.XPATH, xpath) if e.is_displayed()]
    log(f"  · 이어보기 버튼 {len(buttons)}개")

    button = buttons[0]
    if COURSE_NAME:
        # 버튼을 감싼 목록 한 칸(li 또는 표의 한 줄 tr)의 글자에 과정명이 들어 있는지 확인
        matches = [
            b for b in buttons
            if COURSE_NAME in b.find_element(By.XPATH, "./ancestor::*[self::li or self::tr][1]").text
        ]
        if not matches:
            raise RuntimeError(f"'{COURSE_NAME}' 과정을 목록에서 찾지 못했습니다.")
        button = matches[0]

    # 이어보기는 강의실을 '새 창(팝업)'으로 연다 → 클릭 전 창 목록을 기억해 두고 새로 생긴 창으로 전환
    before = set(driver.window_handles)
    safe_click(driver, button)
    log("10) 강의실 창이 열리길 기다리는 중...")
    try:
        wait.until(lambda d: len(set(d.window_handles) - before) > 0)
    except TimeoutException:
        raise RuntimeError(
            f"이어보기를 눌렀지만 {TIMEOUT}초 동안 강의실 창이 열리지 않았습니다. "
            "크롬 팝업 차단이나 다른 알림창이 가리고 있는지 확인하세요."
        )
    driver.switch_to.window((set(driver.window_handles) - before).pop())
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete" and d.title)
    log(f"  · 강의실: {driver.title}")

    log("11) '동영상 재생하기' 클릭")
    play = wait.until(EC.element_to_be_clickable(
        (By.XPATH, "//*[(self::a or self::button) and (normalize-space()='동영상 재생하기' or @title='동영상 재생하기')]")
    ))
    safe_click(driver, play)

    # 실제로 재생되는지 확인 (video 태그의 paused 값)
    try:
        WebDriverWait(driver, 10).until(
            lambda d: d.execute_script("return [...document.querySelectorAll('video')].some(v => !v.paused)")
        )
        log("연수 동영상 재생 시작!")
    except TimeoutException:
        log("재생 버튼은 눌렀지만 재생 상태를 확인하지 못했습니다. 강의실 창을 확인해 주세요.")


def check_only() -> int:
    """--check: 로그인하지 않고 입력칸을 제대로 찾는지만 확인 (찾은 칸을 빨간 테두리로 표시)"""
    driver = open_browser()
    log(f"[점검 모드] {LOGIN_URL}")
    driver.get(LOGIN_URL)
    time.sleep(1)
    if is_logged_in(driver):
        log("이미 로그인된 상태라 로그인 칸이 숨겨져 있습니다. (봇 프로필에 쿠키가 남아 있음)")
        return 0
    try:
        id_box, pw_box = find_login_fields(driver)
    except Exception as e:
        log(f"오류: {err_text(e)}")
        return 1
    for el in (id_box, pw_box):
        driver.execute_script("arguments[0].style.outline='3px solid red';", el)
    log("입력칸을 찾았습니다. 브라우저에서 빨간 테두리를 확인하세요. (아무것도 입력하지 않았습니다)")
    return 0


def run(user_id: str = "", user_pw: str = "", course_name: str = "") -> int:
    """GUI 프로그램이 호출하는 진입점: 로그인 → 수강과정 → 이어보기 → 재생.
    반환값 0 = 성공, 그 외 = 실패한 단계 번호"""
    global USER_ID, USER_PW, COURSE_NAME
    USER_ID = user_id or USER_ID
    USER_PW = user_pw or USER_PW
    COURSE_NAME = course_name or COURSE_NAME
    try:
        return _run_steps()
    except Exception as e:
        log(f"오류: {err_text(e)}")
        return 1


def main() -> int:
    if "--check" in sys.argv:
        return check_only()
    return _run_steps()


def _run_steps() -> int:
    driver = open_browser()
    try:
        ok = login(driver)
    except Exception as e:  # 학습용: 어디서 막혔는지 그대로 보여줌
        log(f"오류: {err_text(e)}")
        return 1

    if ok:
        log(f"로그인 성공! 현재 페이지: {driver.title} ({driver.current_url})")
        try:
            open_my_courses(driver)
        except Exception as e:
            log(f"수강과정 페이지로 이동하다 오류: {err_text(e)}")
            return 3
        try:
            start_lecture(driver)
        except Exception as e:
            log(f"연수 시작 단계에서 오류: {err_text(e)}")
            return 4
        return 0
    log("로그인 성공을 확인하지 못했습니다. 브라우저 화면을 확인해 주세요.")
    log("  (성공했는데 이 메시지가 나오면 .env 의 SUCCESS_TEXT 를 조정하세요)")
    return 2


if __name__ == "__main__":
    sys.exit(main())
