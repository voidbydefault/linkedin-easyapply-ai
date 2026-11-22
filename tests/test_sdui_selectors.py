import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from selenium.webdriver.common.by import By

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.bot.bot import LinkedinEasyApply
from app.bot.forms import ApplicationForm
from app.bot.scout_bot import ScoutBot


class FakeElement:
    """Minimal Selenium WebElement stub supporting CSS/class/tag/id/xpath lookups."""

    def __init__(
        self,
        tag="div",
        text="",
        attrs=None,
        children=None,
        displayed=True,
        enabled=True,
        selected=False,
    ):
        self.tag_name = tag
        self.text = text
        self.attrs = dict(attrs or {})
        self.children = list(children or [])
        self._displayed = displayed
        self._enabled = enabled
        self._selected = selected
        self.parent = None
        for ch in self.children:
            ch.parent = self
        self.clicked = False
        self.sent_keys = []
        self.cleared = False

    def get_attribute(self, name):
        return self.attrs.get(name)

    def is_displayed(self):
        return self._displayed

    def is_enabled(self):
        return self._enabled

    def is_selected(self):
        return self._selected

    def click(self):
        self.clicked = True

    def clear(self):
        self.cleared = True

    def send_keys(self, val):
        self.sent_keys.append(val)

    def _walk(self):
        for ch in self.children:
            yield ch
            yield from ch._walk()

    def _matches_css_part(self, el, sel):
        sel = sel.strip()
        if not sel:
            return False
        if sel.startswith("."):
            cls = el.attrs.get("class", "").split()
            return sel[1:] in cls
        if sel.startswith("#"):
            return el.attrs.get("id") == sel[1:]

        tag_part = sel
        preds = []
        if "[" in sel and sel.endswith("]"):
            first_idx = sel.index("[")
            tag_part = sel[:first_idx]
            rest = sel[first_idx:]
            while rest.startswith("["):
                end = rest.index("]")
                preds.append(rest[1:end])
                rest = rest[end + 1 :]

        if tag_part and el.tag_name.lower() != tag_part.lower():
            return False

        for pred in preds:
            if "^=" in pred:
                k, v = pred.split("^=", 1)
                v = v.strip("'\"")
                if not str(el.attrs.get(k, "")).startswith(v):
                    return False
            elif "*=" in pred:
                k, v = pred.split("*=", 1)
                v = v.strip("'\"")
                if v not in str(el.attrs.get(k, "")):
                    return False
            elif "=" in pred:
                k, v = pred.split("=", 1)
                v = v.strip("'\"")
                if str(el.attrs.get(k, "")) != v:
                    return False
            else:
                if pred not in el.attrs:
                    return False
        return True

    def _matches_selector(self, el, selector):
        for alt in selector.split(","):
            alt = alt.strip()
            parts = alt.split()
            if len(parts) == 1:
                if self._matches_css_part(el, parts[0]):
                    return True
            elif len(parts) == 2:
                if self._matches_css_part(el, parts[1]):
                    anc = el.parent
                    while anc is not None:
                        if self._matches_css_part(anc, parts[0]):
                            return True
                        anc = anc.parent
        return False

    def find_elements(self, by, value):
        if by == By.XPATH:
            if value == "..":
                return [self.parent] if self.parent is not None else []
            if "phoneNumber" in value or '@type="tel"' in value:
                return [
                    el
                    for el in self._walk()
                    if el.tag_name.lower() == "input"
                    and ("phoneNumber" in str(el.attrs.get("id", "")) or el.attrs.get("type") == "tel")
                ]
            if "stay up to date" in value or "follow" in value:
                return [
                    el
                    for el in self._walk()
                    if el.tag_name.lower() == "label"
                    and ("stay up to date" in el.text.lower() or "follow " in el.text.lower())
                ]
            return []

        results = []
        for el in self._walk():
            if by == By.TAG_NAME and el.tag_name.lower() == value.lower():
                results.append(el)
            elif by == By.CLASS_NAME and value in el.attrs.get("class", "").split():
                results.append(el)
            elif by == By.ID and el.attrs.get("id") == value:
                results.append(el)
            elif by == By.CSS_SELECTOR and self._matches_selector(el, value):
                results.append(el)
        return results

    def find_element(self, by, value):
        res = self.find_elements(by, value)
        if not res:
            raise Exception(f"Element not found: {by}={value}")
        return res[0]


class FakeBrowser(FakeElement):
    def __init__(self, children=None):
        super().__init__(tag="document", children=children or [])
        self.visited_urls = []
        self.wait_values = []

    def implicitly_wait(self, sec):
        self.wait_values.append(sec)

    def execute_script(self, script, *args):
        return None

    def get(self, url):
        self.visited_urls.append(url)


def _make_bot(browser, tmp_dir, cls=LinkedinEasyApply):
    tmp_path = Path(tmp_dir)
    params = {
        "email": "test@example.com",
        "password": "secret",
        "locations": ["United States"],
        "positions": ["R&D Director"],
        "personalInfo": {"Mobile Phone Number": "555123456"},
        "uploads": {"resume": str(tmp_path / "resume.pdf")},
        "checkboxes": {},
        "languages": {},
    }
    ai_config = {
        "ai_settings": {
            "batch_size": 5,
            "application_match_threshold": 70,
            "ban_safe": False,
        }
    }
    ai_handler = MagicMock()
    ai_handler.check_heuristics.return_value = None
    ai_handler.evaluate_batch.return_value = {}
    with patch("os.getcwd", return_value=str(tmp_path)):
        return cls(params, browser, ai_config, ai_handler, "Candidate Profile", ["R&D Director"])


def _build_sdui_card(job_id, title, company, location="Metropolis"):
    title_p = FakeElement(
        tag="p",
        text=f"Selected, {title} (Verified job)\n{title}",
        children=[
            FakeElement(tag="span", text=f"Selected, {title} (Verified job)"),
            FakeElement(tag="span", attrs={"aria-hidden": "true"}, text=title),
        ],
    )
    company_p = FakeElement(tag="p", text=company)
    loc_p = FakeElement(tag="p", text=location)
    dismiss_btn = FakeElement(tag="button", attrs={"aria-label": f"Dismiss {title} job"})
    return FakeElement(
        tag="div",
        attrs={"componentkey": f"job-card-component-ref-{job_id}"},
        children=[title_p, company_p, loc_p, dismiss_btn],
    )


class TestSduiSelectors(unittest.TestCase):
    def test_url_encoding_handles_ampersand_and_spaces(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            browser = FakeBrowser()
            bot = _make_bot(browser, tmp_dir)
            bot.next_job_page("R&D Director", "&location=United States", 0)
            self.assertEqual(len(browser.visited_urls), 1)
            url = browser.visited_urls[0]
            self.assertIn("keywords=R%26D+Director", url)
            self.assertIn("location=United+States", url)
            self.assertIn("start=0", url)

    def test_sdui_job_card_extraction_and_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            sdui_card = _build_sdui_card("9000000001", "Senior Advisor", "Example Corp")
            legacy_card = FakeElement(
                tag="li",
                attrs={"class": "scaffold-layout__list-item"},
                children=[
                    FakeElement(
                        tag="a",
                        attrs={
                            "class": "job-card-list__title--link",
                            "href": "https://www.linkedin.com/jobs/view/1234567890/?trackingId=abc",
                        },
                        text="Group Treasurer",
                    ),
                    FakeElement(tag="span", attrs={"class": "artdeco-entity-lockup__subtitle"}, text="ACME Corp"),
                ],
            )
            browser = FakeBrowser([sdui_card, legacy_card])
            bot = _make_bot(browser, tmp_dir)

            cards = bot._get_job_cards(timeout=0)
            self.assertEqual(len(cards), 1)
            title, company, link, job_id = bot._extract_card_info(sdui_card)
            self.assertEqual(title, "Senior Advisor")
            self.assertEqual(company, "Example Corp")
            self.assertEqual(link, "https://www.linkedin.com/jobs/view/9000000001/")
            self.assertEqual(job_id, "9000000001")

            browser_legacy = FakeBrowser([legacy_card])
            bot_legacy = _make_bot(browser_legacy, tmp_dir)
            legacy_cards = bot_legacy._get_job_cards(timeout=0)
            self.assertEqual(len(legacy_cards), 1)
            l_title, l_company, l_link, l_id = bot_legacy._extract_card_info(legacy_cards[0])
            self.assertEqual(l_title, "Group Treasurer")
            self.assertEqual(l_company, "ACME Corp")
            self.assertEqual(l_link, "https://www.linkedin.com/jobs/view/1234567890/")
            self.assertEqual(l_id, "1234567890")

    def test_exact_match_banner_does_not_abort_and_batch_flushes_at_end(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            card1 = _build_sdui_card("9000000001", "Finance Manager", "Acme Labs", "Remote")
            card2 = _build_sdui_card("9000000002", "Risk Analyst", "Globex Inc", "Austin")
            banner = FakeElement(
                tag="div",
                text="We had trouble finding exact matches, but here are results that share similar criteria with your search.",
            )
            details = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="About the job\nLooking for Finance Manager...",
                children=[
                    FakeElement(tag="button", text="Easy Apply", attrs={"aria-label": "Easy Apply to this job"}),
                ],
            )
            browser = FakeBrowser([banner, card1, card2, details])
            bot = _make_bot(browser, tmp_dir, cls=ScoutBot)
            bot.title_blacklist = ["risk"]
            bot.ai_handler.evaluate_batch.return_value = {
                "https://www.linkedin.com/jobs/view/9000000001/": {"score": 88, "reason": "Strong fit"}
            }

            with patch("app.bot.scout_bot.human_sleep"), patch("app.bot.scout_bot.simulate_reading"), patch(
                "app.bot.bot.smart_click"
            ):
                bot.apply_jobs("United States")

            bot.ai_handler.evaluate_batch.assert_called_once()
            scout_df = bot.db.get_scout_jobs()
            self.assertEqual(len(scout_df), 1)
            self.assertEqual(scout_df.iloc[0]["title"], "Finance Manager")

    def test_duplicate_page_detection_stops_infinite_pagination(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            card1 = _build_sdui_card("9000000001", "Finance Manager", "Acme Labs")
            browser = FakeBrowser([card1])
            bot = _make_bot(browser, tmp_dir, cls=ScoutBot)
            bot._seen_search_job_ids = {"9000000001"}

            with self.assertRaisesRegex(Exception, "No more jobs"):
                bot.apply_jobs("United States")

    def test_sdui_eligibility_states(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pane_ready = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                children=[FakeElement(tag="button", text="Easy Apply", attrs={"aria-label": "Easy Apply to this job"})],
            )
            bot = _make_bot(FakeBrowser([pane_ready]), tmp_dir)
            self.assertEqual(bot.check_job_eligibility(), "Ready")

            pane_applied = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="Applied 2 days ago",
            )
            bot_applied = _make_bot(FakeBrowser([pane_applied]), tmp_dir)
            self.assertEqual(bot_applied.check_job_eligibility(), "Already Applied")

            pane_ext = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="Example Corp\nApply\nSave",
                children=[FakeElement(tag="button", text="Apply")],
            )
            bot_ext = _make_bot(FakeBrowser([pane_ext]), tmp_dir)
            self.assertEqual(bot_ext.check_job_eligibility(), "Not Easy Apply (Button missing)")

            pane_limit = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="We limit daily submissions to keep applications fair.",
            )
            bot_limit = _make_bot(FakeBrowser([pane_limit]), tmp_dir)
            with self.assertRaisesRegex(Exception, "API_LIMIT_REACHED"):
                bot_limit.check_job_eligibility()

    def test_sdui_application_form_modal_resume_errors_and_close(self):
        dismiss_btn = FakeElement(tag="button", attrs={"aria-label": "Dismiss"})
        next_btn = FakeElement(tag="button", text="Next")
        discard_btn = FakeElement(tag="button", text="Discard")
        resume_ref = FakeElement(tag="div", attrs={"id": "easyApplyUploadedResumeRef"})
        download_link = FakeElement(tag="a", attrs={"aria-label": "Download sample_resume.pdf"})
        empty_err = FakeElement(tag="div", attrs={"id": "error-message-_r_k1_"}, text="")
        notif_badge = FakeElement(tag="h2", attrs={"role": "alert"}, text="0 notifications")

        sdui_screen = FakeElement(
            tag="div",
            attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.easyapply.EasyApply"},
            text="Resume*\nPDF\nsample_resume.pdf\n11/22/2026",
            children=[resume_ref, download_link, empty_err, next_btn],
        )
        dialog = FakeElement(
            tag="dialog",
            attrs={"data-testid": "dialog", "open": ""},
            children=[dismiss_btn, sdui_screen, notif_badge, discard_btn],
        )
        browser = FakeBrowser([dialog])
        form = ApplicationForm(browser, MagicMock(), "Profile", {"personalInfo": {}, "uploads": {"resume": "x.pdf"}})

        self.assertIs(form._get_modal_container(), sdui_screen)
        self.assertIs(form.find_primary_button(), next_btn)
        self.assertFalse(form.check_for_errors())

        form.send_resume(sdui_screen)

        empty_err.text = "Please enter a valid answer"
        self.assertTrue(form.check_for_errors())

        with patch("app.bot.forms.human_sleep"):
            form.close_modal()
        self.assertTrue(dismiss_btn.clicked)
        self.assertTrue(discard_btn.clicked)


if __name__ == "__main__":
    unittest.main()
