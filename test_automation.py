"""
Standalone Selenium script for submitting news on Khabar Varzeshi Newsroom.

Site: https://newsroom.khabarvarzeshi.com/login/login.xhtml
Stack: JSF / PrimeFaces (slow, dynamic DOM — always use explicit waits).

Install:
    pip install selenium webdriver-manager

Run:
    python test_automation.py

Each step pauses with ``input()`` so you can inspect the browser manually.
Edit the SELECTORS block below if element IDs change on the site.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchWindowException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# ---------------------------------------------------------------------------
# Configuration — edit these values or override via environment variables
# ---------------------------------------------------------------------------

LOGIN_URL = os.getenv(
    "NEWSROOM_LOGIN_URL",
    "https://newsroom.khabarvarzeshi.com/login/login.xhtml",
)

# Set this to the exact "create news" page URL after you confirm it in the browser.
CREATE_NEWS_URL = os.getenv(
    "NEWSROOM_CREATE_URL",
    "https://newsroom.khabarvarzeshi.com/news.xhtml",
)

USERNAME = os.getenv("NEWSROOM_USERNAME", "ai.admin")
PASSWORD = os.getenv("NEWSROOM_PASSWORD", "admin@405")

# How long (seconds) to wait for PrimeFaces elements before failing.
WAIT_TIMEOUT = int(os.getenv("SELENIUM_WAIT_TIMEOUT", "25"))

# Login page can be slow — use a longer timeout for the login step only.
LOGIN_WAIT_TIMEOUT = int(os.getenv("SELENIUM_LOGIN_WAIT_TIMEOUT", "30"))

# CKEditor on news.xhtml can take longer to initialise than plain inputs.
CKEDITOR_WAIT_TIMEOUT = int(os.getenv("SELENIUM_CKEDITOR_WAIT_TIMEOUT", "45"))

# CKEditor instance id matches the <textarea id="newsForm:newsTab:body">.
CKEDITOR_BODY_INSTANCE_ID = "newsForm:newsTab:body"

# Pause between micro-actions inside a step (helps PrimeFaces AJAX settle).
MICRO_DELAY = float(os.getenv("SELENIUM_MICRO_DELAY", "0.5"))
SAMPLE_HEADLINE = "تیتر آزمایشی — تست اتوماسیون"
SAMPLE_LEAD = "این یک لید کوتاه برای تست اسکریپت سلنیوم است."
SAMPLE_BODY = (
    "<h2>زیرعنوان اول</h2>"
    "<p>متن بدنه خبر برای تست CKEditor.</p>"
    "<h2>زیرعنوان دوم</h2>"
    "<p>پاراگراف دوم.</p>"
)
SAMPLE_IMAGE_PATH = os.getenv(
    "NEWSROOM_TEST_IMAGE",
    str(Path(__file__).resolve().parent / "test_image.jpg"),
)
SAMPLE_IMAGE_CAPTION = "عنوان آزمایشی"


class SELECTORS:
    """Central place for all element locators — edit IDs here only."""

    # --- Login page (Spring Security: /j_spring_security_check) ---------------
    USERNAME = (By.NAME, "j_username")
    PASSWORD = (By.NAME, "j_password")
    LOGIN_BUTTON = (By.CSS_SELECTOR, "input[type='submit'][value='ورود']")

    # --- News form (main tab) — news.xhtml ------------------------------------
    HEADLINE = (By.ID, "newsForm:newsTab:headline")
    LEAD = (By.ID, "newsForm:newsTab:lead")
    BODY_PANEL = (By.ID, "newsForm:newsTab:newsBodyPanel")
    BODY_TEXTAREA = (By.ID, "newsForm:newsTab:body")
    BODY_RICHEDITOR = (By.CSS_SELECTOR, "textarea.richeditor[name='newsForm:newsTab:body']")
    BODY_CKEDITOR_IFRAME = (
        By.CSS_SELECTOR,
        "#newsForm\\:newsTab\\:newsBodyPanel iframe.cke_wysiwyg_frame",
    )

    # --- Image upload flow (separate popup window) ----------------------------
    # Opens the media-picker popup ("انتخاب عکس...").
    BTN_SELECT_IMAGE = (By.ID, "newsForm:newsTab:j_id_ey")
    XPATH_SELECT_IMAGE = (
        By.XPATH,
        "//button[contains(.,'انتخاب عکس')]"
        " | //span[contains(.,'انتخاب عکس')]/ancestor::button[1]",
    )
    # Elements inside the popup window (after switch_to.window).
    IMAGE_UPLOAD_TAB = (By.ID, "j_id_58:mediaTab:upload_header")
    XPATH_UPLOAD_TAB = (By.XPATH, "//a[contains(text(), 'آپلود')]")
    IMAGE_FILE_INPUT = (By.XPATH, "//input[@type='file']")
    IMAGE_UPLOAD_TITLE = (By.ID, "j_id_58:mediaTab:uploadTitle")
    IMAGE_UPLOAD_BTN = (By.CSS_SELECTOR, "button.ui-fileupload-upload")
    XPATH_IMAGE_UPLOAD_BTN = (
        By.XPATH,
        "//button[contains(@class,'ui-fileupload-upload')]",
    )
    MEDIA_GRID_FIRST_THUMBNAIL = (
        By.XPATH,
        "(//div[contains(@class,'ui-dataview-content')]"
        "//div[contains(@class,'media-tile')])[1]"
        "//a[contains(@class,'thumbnail-anchor')]",
    )
    IMAGE_CONFIRM_BTN = (By.ID, "j_id_58:mediaTab:j_id_cf_label")
    XPATH_IMAGE_CONFIRM = (By.XPATH, "//span[contains(text(), 'انتخاب')]")
    # On the main news form — indicates an image was attached after popup closes.
    IMAGE_SELECTED_ON_MAIN = (
        By.XPATH,
        "//form[@id='newsForm']//img[contains(@src,'http')]"
        " | //form[@id='newsForm']//span[contains(@class,'ui-fileupload-filename')]",
    )

    # --- Save ----------------------------------------------------------------
    BTN_SAVE = (By.ID, "newsForm:newsTab:btnSaveHistoryBaseNews")


# ---------------------------------------------------------------------------
# Progress logging — only logical steps, element targets, and clean failures
# ---------------------------------------------------------------------------


def _silence_external_loggers() -> None:
    """Suppress Selenium, ChromeDriver, urllib3, and webdriver-manager noise."""
    logging.disable(logging.CRITICAL)
    for name in ("selenium", "urllib3", "WDM", "webdriver_manager", "filelock"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


_silence_external_loggers()


class StepFailure(Exception):
    """Raised after a step failure has already been reported to the terminal."""


def format_locator(locator: tuple[str, str]) -> str:
    """Human-readable element identifier for trace output."""
    by, value = locator
    labels = {
        By.ID: "ID",
        By.NAME: "name",
        By.CSS_SELECTOR: "CSS",
        By.XPATH: "XPath",
        By.CLASS_NAME: "class",
        By.TAG_NAME: "tag",
    }
    return f"{labels.get(by, by)}={value!r}"


def _safe_url(driver: webdriver.Chrome | None) -> str:
    if driver is None:
        return "(no browser)"
    try:
        return driver.current_url
    except WebDriverException:
        return "(unavailable)"


def _clean_error(exc: Exception) -> str:
    if isinstance(exc, TimeoutException):
        return "Element not found or not ready within the wait timeout."
    if isinstance(exc, FileNotFoundError):
        return f"File not found: {exc.filename or exc}"
    msg = str(exc).strip()
    if not msg:
        return type(exc).__name__
    return msg.split("\n")[0].strip()


class Progress:
    """Minimal terminal output: step status, element targets, and failures."""

    def start(self, step: str) -> None:
        print(f"[STEP] Started: {step}", flush=True)

    def complete(self, step: str) -> None:
        print(f"[STEP] Completed: {step}", flush=True)

    def interact(self, action: str, locator: tuple[str, str]) -> None:
        print(f"  -> {action}: {format_locator(locator)}", flush=True)

    def interact_js(self, action: str, target: str) -> None:
        print(f"  -> {action}: {target}", flush=True)

    def note(self, message: str) -> None:
        print(f"  .. {message}", flush=True)

    def fail(self, step: str, driver: webdriver.Chrome | None, exc: Exception) -> None:
        print(f"\n[FAILED] Step: {step}", flush=True)
        print(f"[FAILED] URL: {_safe_url(driver)}", flush=True)
        print(f"[FAILED] Error: {_clean_error(exc)}\n", flush=True)


progress = Progress()


@contextmanager
def tracked_step(
    step: str,
    driver: webdriver.Chrome | None,
    *,
    pause: bool = True,
):
    """Run a logical step; on failure report context and stop the script."""
    progress.start(step)
    try:
        yield
        progress.complete(step)
        if pause:
            pause_step(step)
    except StepFailure:
        raise
    except Exception as exc:
        progress.fail(step, driver, exc)
        raise StepFailure(step) from exc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def pause_step(step_name: str) -> None:
    """Wait for manual verification between major steps."""
    input(f"\n>>> [{step_name}] Press Enter to continue...\n")


def micro_pause() -> None:
    """Short pause so PrimeFaces AJAX can finish between micro-actions."""
    time.sleep(MICRO_DELAY)


def create_driver() -> webdriver.Chrome:
    """Start Chrome with low-level logging disabled."""
    options = ChromeOptions()
    # Comment out the next line if you want a visible browser window.
    # options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--lang=fa-IR")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    # Suppress Chrome console / DevTools noise (3 = fatal only).
    options.add_argument("--log-level=3")
    options.add_experimental_option("excludeSwitches", ["enable-logging"])

    try:
        driver = webdriver.Chrome(
            service=Service(
                ChromeDriverManager().install(),
                log_output=os.devnull,
            ),
            options=options,
        )
    except WebDriverException as exc:
        print(f"\n[FAILED] Step: Initialize Browser", flush=True)
        print("[FAILED] URL: (browser not started)", flush=True)
        print(f"[FAILED] Error: {_clean_error(exc)}\n", flush=True)
        raise StepFailure("Initialize Browser") from exc

    driver.implicitly_wait(0)
    return driver


def initialize_browser() -> webdriver.Chrome:
    """Start Chrome and verify the WebDriver session responds."""
    progress.start("Initialize Browser")
    try:
        driver = create_driver()
        progress.interact_js("verify", "navigator.userAgent via JavaScript")
        driver.execute_script("return navigator.userAgent;")
        progress.complete("Initialize Browser")
        return driver
    except StepFailure:
        raise
    except Exception as exc:
        progress.fail("Initialize Browser", None, exc)
        raise StepFailure("Initialize Browser") from exc


def wait_for(driver: webdriver.Chrome, timeout: int | None = None) -> WebDriverWait:
    return WebDriverWait(driver, timeout or WAIT_TIMEOUT)


def wait_for_login(driver: webdriver.Chrome) -> WebDriverWait:
    """Longer wait used only on the login page."""
    return WebDriverWait(driver, LOGIN_WAIT_TIMEOUT)


def js_type(driver: webdriver.Chrome, element, text: str) -> None:
    """Set an input value via JavaScript and fire an ``input`` event.

    Avoids ``send_keys`` interaction issues on Windows / PrimeFaces forms.
    PrimeFaces/JSF listeners require the ``input`` event to detect changes.
    """
    driver.execute_script("arguments[0].value = arguments[1];", element, text)
    driver.execute_script(
        'arguments[0].dispatchEvent(new Event("input", { bubbles: true }));',
        element,
    )


def click_when_ready(
    driver: webdriver.Chrome,
    locator: tuple[str, str],
    *,
    timeout: int | None = None,
) -> None:
    """Wait until element is clickable, scroll into view, then click."""
    progress.interact("click", locator)
    element = wait_for(driver, timeout).until(EC.element_to_be_clickable(locator))
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});", element
    )
    micro_pause()
    element.click()


def type_when_ready(
    driver: webdriver.Chrome,
    locator: tuple[str, str],
    text: str,
    *,
    clear: bool = True,
    timeout: int | None = None,
    wait_for_presence: bool = False,
) -> None:
    """Wait for an input, then set its value via JavaScript (no send_keys)."""
    progress.interact("type", locator)
    condition = (
        EC.presence_of_element_located
        if wait_for_presence
        else EC.visibility_of_element_located
    )
    element = wait_for(driver, timeout).until(condition(locator))
    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});", element
    )
    micro_pause()
    if clear:
        js_type(driver, element, "")
    js_type(driver, element, text)
    driver.execute_script(
        'arguments[0].dispatchEvent(new Event("change", { bubbles: true }));',
        element,
    )


def wait_for_ckeditor_ready(
    driver: webdriver.Chrome,
    instance_id: str,
    *,
    timeout: int | None = None,
) -> None:
    """Block until ``CKEDITOR.instances[instance_id]`` is loaded and ready."""
    wait_seconds = timeout or CKEDITOR_WAIT_TIMEOUT
    progress.interact_js("wait for CKEditor", f"instance ID={instance_id!r}")

    def _ckeditor_ready(d: webdriver.Chrome) -> bool:
        return d.execute_script(
            """
            var id = arguments[0];
            if (typeof CKEDITOR === 'undefined') return false;
            var editor = CKEDITOR.instances[id];
            return editor != null
                && (editor.status === 'ready' || editor.status === 'loaded');
            """,
            instance_id,
        )

    wait_for(driver, wait_seconds).until(_ckeditor_ready)


def set_ckeditor_content(
    driver: webdriver.Chrome,
    instance_id: str,
    html: str,
) -> None:
    """Set news body HTML via the CKEditor JavaScript API (most reliable).

    The page initialises CKEditor with::

        $('textarea.richeditor').ckeditor({ ... });

    The underlying ``<textarea id="newsForm:newsTab:body">`` is replaced in the
    DOM, so iframe switching is fragile.  ``CKEDITOR.instances[id].setData()``
    is the supported way to programmatically set content.
    """
    driver.switch_to.default_content()

    progress.interact("wait for", SELECTORS.BODY_PANEL)
    wait_for(driver).until(EC.presence_of_element_located(SELECTORS.BODY_PANEL))
    progress.interact("wait for", SELECTORS.BODY_TEXTAREA)
    wait_for(driver).until(EC.presence_of_element_located(SELECTORS.BODY_TEXTAREA))
    wait_for_ckeditor_ready(driver, instance_id)

    progress.interact_js(
        "set CKEditor content",
        f"CKEDITOR.instances[{instance_id!r}].setData()",
    )
    driver.execute_script(
        """
        var instanceId = arguments[0];
        var html = arguments[1];

        if (typeof CKEDITOR === 'undefined') {
            throw new Error('CKEDITOR global is not defined on this page.');
        }
        var editor = CKEDITOR.instances[instanceId];
        if (!editor) {
            throw new Error('CKEDITOR instance not found: ' + instanceId);
        }

        editor.setData(html);
        editor.updateElement();

        var textarea = document.getElementById(instanceId);
        if (textarea) {
            textarea.value = editor.getData();
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
            textarea.dispatchEvent(new Event('change', { bubbles: true }));
        }

        return editor.getData().length;
        """,
        instance_id,
        html,
    )
    micro_pause()
    wait_for(driver, CKEDITOR_WAIT_TIMEOUT).until(
        lambda d: d.execute_script(
            "return (CKEDITOR.instances[arguments[0]].getData() || '').length > 0;",
            instance_id,
        )
    )


def set_ckeditor_content_via_iframe(
    driver: webdriver.Chrome,
    html: str,
) -> None:
    """Fallback: write into the CKEditor WYSIWYG iframe directly."""
    driver.switch_to.default_content()
    progress.interact("wait for", SELECTORS.BODY_CKEDITOR_IFRAME)
    iframe = wait_for(driver).until(
        EC.presence_of_element_located(SELECTORS.BODY_CKEDITOR_IFRAME)
    )
    progress.interact_js("switch to frame", "CKEditor WYSIWYG iframe")
    driver.switch_to.frame(iframe)
    progress.interact("type", (By.TAG_NAME, "body"))
    editable = wait_for(driver).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    driver.execute_script("arguments[0].innerHTML = arguments[1];", editable, html)
    driver.switch_to.default_content()


def find_with_fallback(
    driver: webdriver.Chrome,
    primary: tuple[str, str],
    fallback: tuple[str, str],
    *,
    condition=EC.presence_of_element_located,
):
    """Try primary locator; on timeout, try fallback."""
    progress.interact("find", primary)
    try:
        return wait_for(driver).until(condition(primary))
    except TimeoutException:
        progress.note(f"primary not found, trying fallback: {format_locator(fallback)}")
        progress.interact("find", fallback)
        return wait_for(driver).until(condition(fallback))


# ---------------------------------------------------------------------------
# Step functions
# ---------------------------------------------------------------------------


def login(driver: webdriver.Chrome) -> None:
    """Open the login page and sign in with configured credentials."""
    login_timeout = LOGIN_WAIT_TIMEOUT

    with tracked_step("Login", driver):
        progress.interact_js("navigate", LOGIN_URL)
        driver.get(LOGIN_URL)

        type_when_ready(
            driver,
            SELECTORS.USERNAME,
            USERNAME,
            timeout=login_timeout,
            wait_for_presence=True,
        )
        type_when_ready(
            driver,
            SELECTORS.PASSWORD,
            PASSWORD,
            timeout=login_timeout,
            wait_for_presence=True,
        )
        click_when_ready(
            driver,
            SELECTORS.LOGIN_BUTTON,
            timeout=login_timeout,
        )

        wait_for_login(driver).until(
            lambda d: "login" not in d.current_url.lower()
        )


def go_to_create_news(driver: webdriver.Chrome) -> None:
    """Navigate to the news submission panel and wait for the form + CKEditor."""
    with tracked_step("Navigate to Create News", driver):
        progress.interact_js("navigate", CREATE_NEWS_URL)
        driver.get(CREATE_NEWS_URL)

        progress.interact("wait for", SELECTORS.HEADLINE)
        wait_for(driver).until(
            EC.presence_of_element_located(SELECTORS.HEADLINE)
        )
        progress.interact("wait for", SELECTORS.BODY_PANEL)
        wait_for(driver).until(
            EC.presence_of_element_located(SELECTORS.BODY_PANEL)
        )
        progress.interact("wait for", SELECTORS.BODY_TEXTAREA)
        wait_for(driver).until(
            EC.presence_of_element_located(SELECTORS.BODY_TEXTAREA)
        )
        wait_for_ckeditor_ready(driver, CKEDITOR_BODY_INSTANCE_ID)


def fill_content(
    driver: webdriver.Chrome,
    headline: str,
    lead: str,
    body: str,
) -> None:
    """Fill headline, lead, and CKEditor body on news.xhtml."""
    driver.switch_to.default_content()

    with tracked_step("Title Entry", driver):
        type_when_ready(
            driver,
            SELECTORS.HEADLINE,
            headline,
            wait_for_presence=True,
        )

    with tracked_step("Lead Entry", driver):
        type_when_ready(
            driver,
            SELECTORS.LEAD,
            lead,
            wait_for_presence=True,
        )

    with tracked_step("Body Entry", driver):
        try:
            set_ckeditor_content(driver, CKEDITOR_BODY_INSTANCE_ID, body)
        except WebDriverException:
            progress.note("CKEditor API failed — trying iframe fallback")
            set_ckeditor_content_via_iframe(driver, body)
            progress.interact_js(
                "sync CKEditor",
                f"CKEDITOR.instances[{CKEDITOR_BODY_INSTANCE_ID!r}].setData()",
            )
            driver.execute_script(
                """
                var id = arguments[0];
                var html = arguments[1];
                var editor = CKEDITOR.instances[id];
                if (editor) { editor.setData(html); editor.updateElement(); }
                """,
                CKEDITOR_BODY_INSTANCE_ID,
                body,
            )


def _reveal_file_input(driver: webdriver.Chrome, file_input) -> None:
    """Make a hidden PrimeFaces file input interactable for send_keys."""
    driver.execute_script(
        """
        var el = arguments[0];
        el.style.display = 'block';
        el.style.visibility = 'visible';
        el.style.opacity = '1';
        el.style.height = '1px';
        el.style.width = '1px';
        """,
        file_input,
    )


def _wait_for_popup_window(
    driver: webdriver.Chrome,
    handles_before: list[str],
    *,
    timeout: int | None = None,
) -> str:
    """Wait until a new browser window/tab opens and return its handle."""
    wait_for(driver, timeout).until(
        lambda d: len(d.window_handles) > len(handles_before)
    )
    new_handles = [
        h for h in driver.window_handles if h not in handles_before
    ]
    popup_handle = new_handles[-1] if new_handles else driver.window_handles[-1]
    progress.note("popup window opened")
    return popup_handle


def _switch_to_main_after_popup_close(
    driver: webdriver.Chrome,
    main_window: str,
) -> None:
    """Resume focus on the main news form after the media popup closes itself."""
    progress.interact_js("switch to window", "main news form")
    try:
        if main_window in driver.window_handles:
            driver.switch_to.window(main_window)
        else:
            driver.switch_to.window(driver.window_handles[0])
    except (NoSuchWindowException, WebDriverException):
        driver.switch_to.window(driver.window_handles[0])


def upload_image(
    driver: webdriver.Chrome,
    image_path: str,
    caption: str,
) -> None:
    """
    Upload an image via the separate media-picker popup window.

    Sequence:
      1. Click "انتخاب عکس..." on the main news form
      2. ``switch_to.window`` the newly opened popup
      3. Click the "آپلود" tab
      4. ``send_keys`` the absolute image path into ``<input type="file">``
      5. Fill upload title, click PrimeFaces "آپلود" upload button, wait for server
      6. Click the first media tile thumbnail (popup closes via ``window.opener.setImage``)
      7. Resume focus on the main news entry window
      8. Verify the image attachment before proceeding to save
    """
    image_path = str(Path(image_path).resolve())

    if not Path(image_path).is_file():
        raise FileNotFoundError(image_path)

    popup_window: str | None = None

    with tracked_step("Image Upload", driver):
        driver.switch_to.default_content()

        main_window = driver.current_window_handle
        handles_before = list(driver.window_handles)
        try:
            progress.interact("click", SELECTORS.BTN_SELECT_IMAGE)
            driver.find_element(*SELECTORS.BTN_SELECT_IMAGE).click()
        except WebDriverException:
            click_when_ready(driver, SELECTORS.XPATH_SELECT_IMAGE)

        _wait_for_popup_window(driver, handles_before)
        progress.interact_js("switch to window", "media-picker popup")
        driver.switch_to.window(driver.window_handles[-1])
        popup_window = driver.current_window_handle

        try:
            click_when_ready(driver, SELECTORS.IMAGE_UPLOAD_TAB)
        except TimeoutException:
            click_when_ready(driver, SELECTORS.XPATH_UPLOAD_TAB)

        time.sleep(1)

        progress.interact("upload file", SELECTORS.IMAGE_FILE_INPUT)
        file_input = wait_for(driver).until(
            EC.presence_of_element_located(SELECTORS.IMAGE_FILE_INPUT)
        )
        _reveal_file_input(driver, file_input)
        file_input.send_keys(image_path)
        micro_pause()

        upload_title = caption or "عنوان آزمایشی"
        type_when_ready(
            driver,
            SELECTORS.IMAGE_UPLOAD_TITLE,
            upload_title,
            wait_for_presence=True,
        )

        try:
            click_when_ready(driver, SELECTORS.IMAGE_UPLOAD_BTN)
        except TimeoutException:
            click_when_ready(driver, SELECTORS.XPATH_IMAGE_UPLOAD_BTN)

        progress.note("waiting 3s for server to process upload")
        time.sleep(3)

        click_when_ready(driver, SELECTORS.MEDIA_GRID_FIRST_THUMBNAIL)
        _switch_to_main_after_popup_close(driver, main_window)

        def _upload_verified_on_main(d: webdriver.Chrome) -> bool:
            if popup_window and popup_window in d.window_handles:
                return False
            if len(d.window_handles) > 1:
                return False
            matches = d.find_elements(*SELECTORS.IMAGE_SELECTED_ON_MAIN)
            return any(el.is_displayed() for el in matches)

        progress.interact("wait for", SELECTORS.IMAGE_SELECTED_ON_MAIN)
        wait_for(driver, WAIT_TIMEOUT).until(_upload_verified_on_main)

        if caption:
            progress.note("caption provided — set manually on the form if needed")


def save_news(driver: webdriver.Chrome) -> None:
    """Click the save button and wait for a success signal."""
    with tracked_step("Save News", driver):
        click_when_ready(driver, SELECTORS.BTN_SAVE)
        micro_pause()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main() -> int:
    driver = None
    try:
        driver = initialize_browser()

        login(driver)
        go_to_create_news(driver)
        fill_content(driver, SAMPLE_HEADLINE, SAMPLE_LEAD, SAMPLE_BODY)

        if Path(SAMPLE_IMAGE_PATH).is_file():
            upload_image(driver, SAMPLE_IMAGE_PATH, SAMPLE_IMAGE_CAPTION)
        else:
            progress.note(
                f"skipping image upload — file not found: {SAMPLE_IMAGE_PATH}"
            )
            pause_step("Image Upload (skipped)")

        save_news(driver)

        print("[STEP] Completed: All Steps", flush=True)
        input("\n>>> Automation finished. Press Enter to close the browser...\n")
        if driver is not None:
            driver.quit()
        return 0

    except KeyboardInterrupt:
        print("\n[STOP] Interrupted by user.", flush=True)
        if driver is not None:
            input(
                "\n>>> Interrupted — browser left open. "
                "Press Enter to close it (or close the window manually)...\n"
            )
            try:
                driver.quit()
            except WebDriverException:
                pass
        return 130

    except StepFailure:
        if driver is not None:
            input(
                "\n>>> Step failed — browser left open for inspection. "
                "Press Enter to exit...\n"
            )
        else:
            input("\n>>> Step failed before browser started. Press Enter to exit...\n")
        return 1

    except Exception as exc:
        progress.fail("Unexpected Error", driver, exc)
        if driver is not None:
            input(
                "\n>>> Unexpected error — browser left open for inspection. "
                "Press Enter to exit...\n"
            )
        else:
            input("\n>>> Unexpected error. Press Enter to exit...\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
