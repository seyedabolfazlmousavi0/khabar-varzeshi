"""Selenium automation for the Khabar Varzeshi newsroom dashboard."""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchWindowException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from core.newsroom.config import NewsroomConfig
from core.newsroom.exceptions import SitePublishError

logger = logging.getLogger(__name__)

CKEDITOR_BODY_INSTANCE_ID = "newsForm:newsTab:body"


class SELECTORS:
    """Central place for all element locators."""

    USERNAME = (By.NAME, "j_username")
    PASSWORD = (By.NAME, "j_password")
    LOGIN_BUTTON = (By.CSS_SELECTOR, "input[type='submit'][value='ورود']")

    HEADLINE = (By.ID, "newsForm:newsTab:headline")
    LEAD = (By.ID, "newsForm:newsTab:lead")
    BODY_PANEL = (By.ID, "newsForm:newsTab:newsBodyPanel")
    BODY_TEXTAREA = (By.ID, "newsForm:newsTab:body")
    BODY_CKEDITOR_IFRAME = (
        By.CSS_SELECTOR,
        "textarea.richeditor[name='newsForm:newsTab:body']",
    )
    BODY_CKEDITOR_IFRAME_FRAME = (
        By.CSS_SELECTOR,
        "#newsForm\\:newsTab\\:newsBodyPanel iframe.cke_wysiwyg_frame",
    )

    BTN_SELECT_IMAGE = (By.ID, "newsForm:newsTab:j_id_ey")
    XPATH_SELECT_IMAGE = (
        By.XPATH,
        "//button[contains(.,'انتخاب عکس')]"
        " | //span[contains(.,'انتخاب عکس')]/ancestor::button[1]",
    )
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
    IMAGE_SELECTED_ON_MAIN = (
        By.XPATH,
        "//form[@id='newsForm']//img[contains(@src,'http')]"
        " | //form[@id='newsForm']//span[contains(@class,'ui-fileupload-filename')]",
    )

    BTN_SAVE = (By.ID, "newsForm:newsTab:btnSaveHistoryBaseNews")


def _silence_external_loggers() -> None:
    for name in ("selenium", "urllib3", "WDM", "webdriver_manager", "filelock"):
        logging.getLogger(name).setLevel(logging.WARNING)


_silence_external_loggers()


def _format_locator(locator: tuple[str, str]) -> str:
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


class NewsroomAutomation:
    """Drive the newsroom UI to submit one article."""

    def __init__(self, config: NewsroomConfig) -> None:
        self.config = config
        self._driver: webdriver.Chrome | None = None

    def publish(
        self,
        *,
        headline: str,
        lead: str,
        body: str,
        image_path: str | None = None,
        image_caption: str | None = None,
    ) -> None:
        driver = None
        try:
            driver = self._initialize_browser()
            self._login(driver)
            self._go_to_create_news(driver)
            self._fill_content(driver, headline, lead, body)

            if image_path:
                self._upload_image(driver, image_path, image_caption or headline)
            else:
                logger.info("Skipping image upload — no local image path provided.")

            self._save_news(driver)
            logger.info("Newsroom publish completed successfully.")
        except SitePublishError:
            raise
        except Exception as exc:
            logger.exception(
                "Unexpected newsroom automation error at URL=%s",
                _safe_url(driver),
            )
            raise SitePublishError(_clean_error(exc)) from exc
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except WebDriverException:
                    pass

    def _micro_pause(self) -> None:
        time.sleep(self.config.micro_delay)

    def _wait_for(
        self,
        driver: webdriver.Chrome,
        timeout: int | None = None,
    ) -> WebDriverWait:
        return WebDriverWait(driver, timeout or self.config.wait_timeout)

    def _wait_for_login(self, driver: webdriver.Chrome) -> WebDriverWait:
        return WebDriverWait(driver, self.config.login_wait_timeout)

    @contextmanager
    def _tracked_step(self, step: str, driver: webdriver.Chrome):
        logger.info("[STEP] Started: %s", step)
        try:
            yield
            logger.info("[STEP] Completed: %s", step)
        except SitePublishError:
            raise
        except Exception as exc:
            logger.error(
                "[FAILED] Step=%s URL=%s Error=%s",
                step,
                _safe_url(driver),
                _clean_error(exc),
            )
            raise SitePublishError(f"{step}: {_clean_error(exc)}") from exc

    def _create_driver(self) -> webdriver.Chrome:
        options = ChromeOptions()
        if self.config.headless:
            options.add_argument("--headless=new")
        options.add_argument("--start-maximized")
        options.add_argument("--disable-notifications")
        options.add_argument("--lang=fa-IR")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
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
            raise SitePublishError(
                f"Initialize Browser: {_clean_error(exc)}"
            ) from exc

        driver.implicitly_wait(0)
        return driver

    def _initialize_browser(self) -> webdriver.Chrome:
        logger.info("[STEP] Started: Initialize Browser")
        try:
            driver = self._create_driver()
            driver.execute_script("return navigator.userAgent;")
            logger.info("[STEP] Completed: Initialize Browser")
            return driver
        except SitePublishError:
            raise
        except Exception as exc:
            logger.error(
                "[FAILED] Step=Initialize Browser URL=(browser not started) Error=%s",
                _clean_error(exc),
            )
            raise SitePublishError(f"Initialize Browser: {_clean_error(exc)}") from exc

    def _js_type(self, driver: webdriver.Chrome, element, text: str) -> None:
        driver.execute_script("arguments[0].value = arguments[1];", element, text)
        driver.execute_script(
            'arguments[0].dispatchEvent(new Event("input", { bubbles: true }));',
            element,
        )

    def _click_when_ready(
        self,
        driver: webdriver.Chrome,
        locator: tuple[str, str],
        *,
        timeout: int | None = None,
    ) -> None:
        logger.debug("click %s", _format_locator(locator))
        element = self._wait_for(driver, timeout).until(
            EC.element_to_be_clickable(locator)
        )
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", element
        )
        self._micro_pause()
        element.click()

    def _type_when_ready(
        self,
        driver: webdriver.Chrome,
        locator: tuple[str, str],
        text: str,
        *,
        clear: bool = True,
        timeout: int | None = None,
        wait_for_presence: bool = False,
    ) -> None:
        logger.debug("type %s", _format_locator(locator))
        condition = (
            EC.presence_of_element_located
            if wait_for_presence
            else EC.visibility_of_element_located
        )
        element = self._wait_for(driver, timeout).until(condition(locator))
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", element
        )
        self._micro_pause()
        if clear:
            self._js_type(driver, element, "")
        self._js_type(driver, element, text)
        driver.execute_script(
            'arguments[0].dispatchEvent(new Event("change", { bubbles: true }));',
            element,
        )

    def _wait_for_ckeditor_ready(
        self,
        driver: webdriver.Chrome,
        instance_id: str,
        *,
        timeout: int | None = None,
    ) -> None:
        wait_seconds = timeout or self.config.ckeditor_wait_timeout

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

        self._wait_for(driver, wait_seconds).until(_ckeditor_ready)

    def _set_ckeditor_content(
        self,
        driver: webdriver.Chrome,
        instance_id: str,
        html: str,
    ) -> None:
        driver.switch_to.default_content()

        self._wait_for(driver).until(
            EC.presence_of_element_located(SELECTORS.BODY_PANEL)
        )
        self._wait_for(driver).until(
            EC.presence_of_element_located(SELECTORS.BODY_TEXTAREA)
        )
        self._wait_for_ckeditor_ready(driver, instance_id)

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
        self._micro_pause()
        self._wait_for(driver, self.config.ckeditor_wait_timeout).until(
            lambda d: d.execute_script(
                "return (CKEDITOR.instances[arguments[0]].getData() || '').length > 0;",
                instance_id,
            )
        )

    def _set_ckeditor_content_via_iframe(
        self,
        driver: webdriver.Chrome,
        html: str,
    ) -> None:
        driver.switch_to.default_content()
        iframe = self._wait_for(driver).until(
            EC.presence_of_element_located(SELECTORS.BODY_CKEDITOR_IFRAME_FRAME)
        )
        driver.switch_to.frame(iframe)
        editable = self._wait_for(driver).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        driver.execute_script("arguments[0].innerHTML = arguments[1];", editable, html)
        driver.switch_to.default_content()

    def _login(self, driver: webdriver.Chrome) -> None:
        login_timeout = self.config.login_wait_timeout

        with self._tracked_step("Login", driver):
            driver.get(self.config.login_url)
            self._type_when_ready(
                driver,
                SELECTORS.USERNAME,
                self.config.username,
                timeout=login_timeout,
                wait_for_presence=True,
            )
            self._type_when_ready(
                driver,
                SELECTORS.PASSWORD,
                self.config.password,
                timeout=login_timeout,
                wait_for_presence=True,
            )
            self._click_when_ready(
                driver,
                SELECTORS.LOGIN_BUTTON,
                timeout=login_timeout,
            )
            self._wait_for_login(driver).until(
                lambda d: "login" not in d.current_url.lower()
            )

    def _go_to_create_news(self, driver: webdriver.Chrome) -> None:
        with self._tracked_step("Navigate to Create News", driver):
            driver.get(self.config.create_url)
            self._wait_for(driver).until(
                EC.presence_of_element_located(SELECTORS.HEADLINE)
            )
            self._wait_for(driver).until(
                EC.presence_of_element_located(SELECTORS.BODY_PANEL)
            )
            self._wait_for(driver).until(
                EC.presence_of_element_located(SELECTORS.BODY_TEXTAREA)
            )
            self._wait_for_ckeditor_ready(driver, CKEDITOR_BODY_INSTANCE_ID)

    def _fill_content(
        self,
        driver: webdriver.Chrome,
        headline: str,
        lead: str,
        body: str,
    ) -> None:
        driver.switch_to.default_content()

        with self._tracked_step("Title Entry", driver):
            self._type_when_ready(
                driver,
                SELECTORS.HEADLINE,
                headline,
                wait_for_presence=True,
            )

        with self._tracked_step("Lead Entry", driver):
            self._type_when_ready(
                driver,
                SELECTORS.LEAD,
                lead,
                wait_for_presence=True,
            )

        with self._tracked_step("Body Entry", driver):
            try:
                self._set_ckeditor_content(driver, CKEDITOR_BODY_INSTANCE_ID, body)
            except WebDriverException:
                logger.warning("CKEditor API failed — trying iframe fallback")
                self._set_ckeditor_content_via_iframe(driver, body)
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

    def _click_media_grid_first_thumbnail(self, driver: webdriver.Chrome) -> None:
        locator = SELECTORS.MEDIA_GRID_FIRST_THUMBNAIL

        def _click_thumbnail(drv: webdriver.Chrome) -> bool:
            try:
                element = drv.find_element(*locator)
                if not element.is_displayed():
                    return False
                drv.execute_script(
                    """
                    var el = arguments[0];
                    el.scrollIntoView({block: 'center'});
                    el.click();
                    """,
                    element,
                )
                return True
            except StaleElementReferenceException:
                return False

        self._wait_for(driver).until(_click_thumbnail)

    def _reveal_file_input(self, driver: webdriver.Chrome, file_input) -> None:
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
        self,
        driver: webdriver.Chrome,
        handles_before: list[str],
        *,
        timeout: int | None = None,
    ) -> str:
        self._wait_for(driver, timeout).until(
            lambda d: len(d.window_handles) > len(handles_before)
        )
        new_handles = [h for h in driver.window_handles if h not in handles_before]
        return new_handles[-1] if new_handles else driver.window_handles[-1]

    def _switch_to_main_after_popup_close(
        self,
        driver: webdriver.Chrome,
        main_window: str,
    ) -> None:
        try:
            if main_window in driver.window_handles:
                driver.switch_to.window(main_window)
            else:
                driver.switch_to.window(driver.window_handles[0])
        except (NoSuchWindowException, WebDriverException):
            driver.switch_to.window(driver.window_handles[0])

    def _upload_image(
        self,
        driver: webdriver.Chrome,
        image_path: str,
        caption: str,
    ) -> None:
        from pathlib import Path

        image_path = str(Path(image_path).resolve())
        if not Path(image_path).is_file():
            raise SitePublishError(f"Image file not found: {image_path}")

        popup_window: str | None = None

        with self._tracked_step("Image Upload", driver):
            driver.switch_to.default_content()

            main_window = driver.current_window_handle
            handles_before = list(driver.window_handles)
            try:
                driver.find_element(*SELECTORS.BTN_SELECT_IMAGE).click()
            except WebDriverException:
                self._click_when_ready(driver, SELECTORS.XPATH_SELECT_IMAGE)

            self._wait_for_popup_window(driver, handles_before)
            driver.switch_to.window(driver.window_handles[-1])
            popup_window = driver.current_window_handle

            try:
                self._click_when_ready(driver, SELECTORS.IMAGE_UPLOAD_TAB)
            except TimeoutException:
                self._click_when_ready(driver, SELECTORS.XPATH_UPLOAD_TAB)

            time.sleep(1)

            file_input = self._wait_for(driver).until(
                EC.presence_of_element_located(SELECTORS.IMAGE_FILE_INPUT)
            )
            self._reveal_file_input(driver, file_input)
            file_input.send_keys(image_path)
            self._micro_pause()

            self._type_when_ready(
                driver,
                SELECTORS.IMAGE_UPLOAD_TITLE,
                caption,
                wait_for_presence=True,
            )

            try:
                self._click_when_ready(driver, SELECTORS.IMAGE_UPLOAD_BTN)
            except TimeoutException:
                self._click_when_ready(driver, SELECTORS.XPATH_IMAGE_UPLOAD_BTN)

            logger.info("Waiting 10s for newsroom server to process image upload")
            time.sleep(10)

            self._click_media_grid_first_thumbnail(driver)
            self._switch_to_main_after_popup_close(driver, main_window)

            def _upload_verified_on_main(d: webdriver.Chrome) -> bool:
                if popup_window and popup_window in d.window_handles:
                    return False
                if len(d.window_handles) > 1:
                    return False
                matches = d.find_elements(*SELECTORS.IMAGE_SELECTED_ON_MAIN)
                return any(el.is_displayed() for el in matches)

            self._wait_for(driver).until(_upload_verified_on_main)

    def _save_news(self, driver: webdriver.Chrome) -> None:
        with self._tracked_step("Save News", driver):
            self._click_when_ready(driver, SELECTORS.BTN_SAVE)
            self._micro_pause()
