import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.common.by import By

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.bot.bot import LinkedinEasyApply
from app.bot.database import JobDatabase
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


class TestJobDeduplication(unittest.TestCase):
    def test_database_canonical_job_id_and_migration_backfill(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "job_history.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE jobs (job_hash TEXT PRIMARY KEY, url TEXT, title TEXT, status TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, reason TEXT)"
            )
            conn.execute(
                "CREATE TABLE scout_jobs (job_hash TEXT PRIMARY KEY, url TEXT, title TEXT, company TEXT, location TEXT, score INTEGER, reason TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, completed BOOLEAN DEFAULT 0)"
            )
            conn.execute(
                "INSERT INTO jobs (job_hash, url, title, status, reason) VALUES (?, ?, ?, ?, ?)",
                ("legacy_hash_1", "https://www.linkedin.com/jobs/view/1234567890/?trackingId=xyz", "Old Job", "Applied", "Success"),
            )
            conn.execute(
                "INSERT INTO scout_jobs (job_hash, url, title, company, location, score, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("scout_hash_1", "https://www.linkedin.com/jobs/view/2234567890/", "Old Match", "Acme", "Remote", 92, "Strong"),
            )
            conn.commit()
            conn.close()

            db = JobDatabase(tmp_dir)

            urls = [
                "https://www.linkedin.com/jobs/view/1234567890/",
                "https://www.linkedin.com/jobs/view/1234567890",
                "https://www.linkedin.com/jobs/view/1234567890/?trackingId=abc",
                "https://sa.linkedin.com/jobs/view/vp-finance-1234567890/",
                "https://www.linkedin.com/jobs/search/?currentJobId=1234567890",
            ]
            hashes = {db.get_job_hash(u) for u in urls}
            self.assertEqual(len(hashes), 1)
            for u in urls:
                self.assertEqual(db.extract_job_id(u), "1234567890")
                self.assertEqual(db.get_job_status(u), ("Applied", "Success"))

            self.assertEqual(
                db.get_scout_status("https://sa.linkedin.com/jobs/view/old-match-2234567890/"),
                ("Scouted-Match", "Strong"),
            )
            self.assertTrue(db.clear_scout_table())
            scout_df = db.get_scout_jobs()
            self.assertEqual(len(scout_df), 0)
            self.assertEqual(
                db.get_scout_status("https://www.linkedin.com/jobs/view/2234567890/"),
                ("Scouted-Match", "Strong"),
            )

    def test_scout_bot_skips_already_scanned_and_preserves_apply_crossover(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            card1 = _build_sdui_card("9000000011", "FP&A Director", "Acme Corp")
            card2 = _build_sdui_card("9000000012", "Hotel GM", "Resort Inc")
            card3 = _build_sdui_card("9000000013", "Group Treasurer", "HoldCo")
            details = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="About the job\nDetailed role description...",
            )

            browser1 = FakeBrowser([card1, card2, details])
            bot1 = _make_bot(browser1, tmp_dir, cls=ScoutBot)
            bot1.ai_handler.evaluate_batch.return_value = {
                "https://www.linkedin.com/jobs/view/9000000011/": {"score": 90, "reason": "Strong fit"},
                "https://www.linkedin.com/jobs/view/9000000012/": {"score": 25, "reason": "Wrong industry"},
            }

            with patch("app.bot.scout_bot.human_sleep"), patch("app.bot.scout_bot.simulate_reading"), patch(
                "app.bot.bot.smart_click"
            ):
                bot1.apply_jobs("United States")

            self.assertEqual(len(bot1.db.get_scout_jobs()), 1)
            self.assertEqual(bot1.db.get_scout_status("https://www.linkedin.com/jobs/view/9000000011/")[0], "Scouted-Match")
            self.assertEqual(bot1.db.get_scout_status("https://www.linkedin.com/jobs/view/9000000012/")[0], "Scouted-LowScore")
            # Scout-only scans must not mark Apply-mode history so Apply mode can still apply to matches later
            self.assertIsNone(bot1.db.get_job_status("https://www.linkedin.com/jobs/view/9000000011/", job_id="9000000011"))

            # Mark card3 as already applied in Apply mode
            bot1.db.mark_job_seen("https://www.linkedin.com/jobs/view/9000000013/", "Group Treasurer", "Applied", "Success", job_id="9000000013")

            browser2 = FakeBrowser([card1, card2, card3, details])
            bot2 = _make_bot(browser2, tmp_dir, cls=ScoutBot)
            with patch("app.bot.scout_bot.human_sleep"), patch("app.bot.scout_bot.simulate_reading"), patch(
                "app.bot.bot.smart_click"
            ) as mock_click:
                bot2.apply_jobs("United States")

            mock_click.assert_not_called()
            bot2.ai_handler.evaluate_batch.assert_not_called()

    def test_same_page_duplicate_cards_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            dup1 = _build_sdui_card("9000000099", "Finance Advisor", "Baker Co")
            dup2 = _build_sdui_card("9000000099", "Finance Advisor", "Baker Co")
            details = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="About the job\nFinance advisor duties...",
                children=[FakeElement(tag="button", text="Easy Apply", attrs={"aria-label": "Easy Apply to this job"})],
            )

            scout_bot = _make_bot(FakeBrowser([dup1, dup2, details]), tmp_dir, cls=ScoutBot)
            scout_bot.ai_handler.evaluate_batch.return_value = {
                "https://www.linkedin.com/jobs/view/9000000099/": {"score": 40, "reason": "Partial fit"}
            }
            with patch("app.bot.scout_bot.human_sleep"), patch("app.bot.scout_bot.simulate_reading"), patch(
                "app.bot.bot.smart_click"
            ):
                scout_bot.apply_jobs("United States")
            self.assertEqual(len(scout_bot.ai_handler.evaluate_batch.call_args[0][0]), 1)

            apply_bot = _make_bot(FakeBrowser([dup1, dup2, details]), tmp_dir, cls=LinkedinEasyApply)
            apply_bot.ai_handler.evaluate_batch.return_value = {
                "https://www.linkedin.com/jobs/view/9000000099/": {"score": 40, "reason": "Partial fit"}
            }
            with patch("app.bot.bot.human_sleep"), patch("app.bot.bot.simulate_reading"), patch(
                "app.bot.bot.smart_click"
            ):
                apply_bot.apply_jobs("United States")
            self.assertEqual(len(apply_bot.ai_handler.evaluate_batch.call_args[0][0]), 1)

    def test_transient_ai_error_and_empty_description_not_marked_seen(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            card_err = _build_sdui_card("9000000077", "Head of Finance", "Alpha")
            details = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="About the job\nReal description",
                children=[FakeElement(tag="button", text="Easy Apply", attrs={"aria-label": "Easy Apply to this job"})],
            )

            scout_bot = _make_bot(FakeBrowser([card_err, details]), tmp_dir, cls=ScoutBot)
            scout_bot.ai_handler.evaluate_batch.return_value = {
                "https://www.linkedin.com/jobs/view/9000000077/": {"score": 0, "reason": "Batch Error / Parsing Failed"}
            }
            with patch("app.bot.scout_bot.human_sleep"), patch("app.bot.scout_bot.simulate_reading"), patch(
                "app.bot.bot.smart_click"
            ):
                scout_bot.apply_jobs("United States")
            self.assertIsNone(scout_bot.db.get_scout_status("https://www.linkedin.com/jobs/view/9000000077/", job_id="9000000077"))

            apply_bot = _make_bot(FakeBrowser([card_err, details]), tmp_dir, cls=LinkedinEasyApply)
            apply_bot.ai_handler.evaluate_batch.return_value = {
                "https://www.linkedin.com/jobs/view/9000000077/": {"score": 0, "reason": "Batch Error / Parsing Failed"}
            }
            with patch("app.bot.bot.human_sleep"), patch("app.bot.bot.simulate_reading"), patch(
                "app.bot.bot.smart_click"
            ):
                apply_bot.apply_jobs("United States")
            self.assertIsNone(apply_bot.db.get_job_status("https://www.linkedin.com/jobs/view/9000000077/", job_id="9000000077"))

            # Empty description should also not be marked seen
            empty_details = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="   ",
                children=[FakeElement(tag="button", text="Easy Apply", attrs={"aria-label": "Easy Apply to this job"})],
            )
            scout_empty = _make_bot(FakeBrowser([card_err, empty_details]), tmp_dir, cls=ScoutBot)
            with patch("app.bot.scout_bot.human_sleep"), patch("app.bot.scout_bot.simulate_reading"), patch(
                "app.bot.bot.smart_click"
            ):
                scout_empty.apply_jobs("United States")
            scout_empty.ai_handler.evaluate_batch.assert_not_called()
            self.assertIsNone(scout_empty.db.get_scout_status("https://www.linkedin.com/jobs/view/9000000077/", job_id="9000000077"))

    def test_card_applied_badge_skipped_before_click_in_scout_and_apply(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            card_exact = _build_sdui_card("9000000201", "Finance Advisor", "Baker Hughes")
            card_exact.children.append(FakeElement(tag="p", text="Applied"))

            card_ago = _build_sdui_card("9000000202", "VP Finance", "Acme Corp")
            card_ago.children.append(FakeElement(tag="p", text="Applied · 2 days ago"))

            card_on_date = _build_sdui_card("9000000203", "Treasurer", "Globex")
            card_on_date.children.append(FakeElement(tag="p", text="Applied on Nov 20"))

            legacy_applied = FakeElement(
                tag="li",
                attrs={"class": "scaffold-layout__list-item", "data-occludable-job-id": "9000000204"},
                children=[
                    FakeElement(
                        tag="a",
                        attrs={"class": "job-card-list__title--link", "href": "https://www.linkedin.com/jobs/view/9000000204/"},
                        text="Finance Director",
                    ),
                    FakeElement(tag="span", attrs={"class": "artdeco-entity-lockup__subtitle"}, text="Initech"),
                    FakeElement(tag="li", attrs={"class": "job-card-container__footer-job-state"}, text="Applied"),
                ],
            )

            scout_bot = _make_bot(FakeBrowser([card_exact, card_ago, card_on_date]), tmp_dir, cls=ScoutBot)
            with patch("app.bot.scout_bot.human_sleep"), patch("app.bot.scout_bot.simulate_reading"), patch(
                "app.bot.bot.smart_click"
            ) as mock_click:
                scout_bot.apply_jobs("United States")

            mock_click.assert_not_called()
            scout_bot.ai_handler.evaluate_batch.assert_not_called()

            scout_legacy = _make_bot(FakeBrowser([legacy_applied]), tmp_dir, cls=ScoutBot)
            with patch("app.bot.scout_bot.human_sleep"), patch("app.bot.scout_bot.simulate_reading"), patch(
                "app.bot.bot.smart_click"
            ) as mock_click_legacy:
                scout_legacy.apply_jobs("United States")

            mock_click_legacy.assert_not_called()
            scout_legacy.ai_handler.evaluate_batch.assert_not_called()

            for jid in ("9000000201", "9000000202", "9000000203", "9000000204"):
                url = f"https://www.linkedin.com/jobs/view/{jid}/"
                self.assertEqual(scout_bot.db.get_scout_status(url, job_id=jid)[0], "Already Applied")
                self.assertEqual(scout_bot.db.get_job_status(url, job_id=jid)[0], "Already Applied")

        with tempfile.TemporaryDirectory() as tmp_dir2:
            card_apply = _build_sdui_card("9000000205", "Finance Advisor", "Baker Hughes")
            card_apply.children.append(FakeElement(tag="p", text="Applied"))
            apply_bot = _make_bot(FakeBrowser([card_apply]), tmp_dir2, cls=LinkedinEasyApply)
            with patch("app.bot.bot.human_sleep"), patch("app.bot.bot.simulate_reading"), patch(
                "app.bot.bot.smart_click"
            ) as mock_click:
                apply_bot.apply_jobs("United States")

            mock_click.assert_not_called()
            apply_bot.ai_handler.evaluate_batch.assert_not_called()
            self.assertEqual(
                apply_bot.db.get_job_status("https://www.linkedin.com/jobs/view/9000000205/", job_id="9000000205")[0],
                "Already Applied",
            )

    def test_applied_in_title_or_company_not_false_positive(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            card = _build_sdui_card("9000000301", "Applied Scientist", "Applied Materials")
            card.children.append(FakeElement(tag="p", text="Viewed · Be an early applicant"))
            details = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="About the job\nResearch scientist role...",
            )
            scout_bot = _make_bot(FakeBrowser([card, details]), tmp_dir, cls=ScoutBot)
            self.assertFalse(scout_bot._is_card_already_applied(card, job_title="Applied Scientist", company="Applied Materials"))
            scout_bot.ai_handler.evaluate_batch.return_value = {
                "https://www.linkedin.com/jobs/view/9000000301/": {"score": 85, "reason": "Strong fit"}
            }
            with patch("app.bot.scout_bot.human_sleep"), patch("app.bot.scout_bot.simulate_reading"), patch(
                "app.bot.bot.smart_click"
            ):
                scout_bot.apply_jobs("United States")
            scout_bot.ai_handler.evaluate_batch.assert_called_once()

    def test_eligibility_detects_applied_on_date(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pane_applied_date = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="Finance Advisor\nApplied on Nov 20",
            )
            bot = _make_bot(FakeBrowser([pane_applied_date]), tmp_dir)
            self.assertEqual(bot.check_job_eligibility(), "Already Applied")

    def test_scout_bot_ignores_easy_apply_filter_in_search_url(self):
        filter_params = {
            "email": "test@example.com",
            "password": "secret",
            "locations": ["United States"],
            "positions": ["CFO"],
            "personalInfo": {"Mobile Phone Number": "555123456"},
            "uploads": {"resume": "resume.pdf"},
            "checkboxes": {},
            "languages": {},
            "lessthanTenApplicants": True,
            "remote": True,
            "date": {"24 hours": True},
            "experienceLevel": {"director": True},
            "jobTypes": {"full-time": True},
        }
        ai_config = {"ai_settings": {"batch_size": 5, "application_match_threshold": 70, "ban_safe": False}}

        with tempfile.TemporaryDirectory() as tmp_dir:
            apply_browser = FakeBrowser()
            scout_browser = FakeBrowser()
            with patch("os.getcwd", return_value=tmp_dir):
                apply_bot = LinkedinEasyApply(filter_params, apply_browser, ai_config, MagicMock(), "Profile", ["CFO"])
                scout_bot = ScoutBot(filter_params, scout_browser, ai_config, MagicMock(), "Profile", ["CFO"])

            self.assertIn("f_AL=true", apply_bot.base_search_url)
            self.assertNotIn("f_AL", scout_bot.base_search_url)
            for expected_param in ("f_EA=true", "f_TPR=r86400", "f_E=5", "f_JT=F", "f_WT=2"):
                self.assertIn(expected_param, scout_bot.base_search_url)

            apply_bot.next_job_page("CFO", "&location=United States", 0)
            scout_bot.next_job_page("CFO", "&location=United States", 0)

            self.assertIn("f_AL=true", apply_browser.visited_urls[-1])
            self.assertNotIn("f_AL", scout_browser.visited_urls[-1])
            for expected_param in ("f_EA=true", "f_TPR=r86400", "f_E=5", "f_JT=F", "f_WT=2"):
                self.assertIn(expected_param, scout_browser.visited_urls[-1])

    def test_scout_bot_processes_non_easy_apply_external_job(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            card = _build_sdui_card("9000000401", "VP Finance", "External Corp")
            details = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="About the job\nLead global finance operations...",
                children=[
                    FakeElement(
                        tag="a",
                        text="Apply",
                        attrs={"aria-label": "Apply on company website", "href": "https://example.com/apply"},
                    )
                ],
            )

            scout_bot = _make_bot(FakeBrowser([card, details]), tmp_dir, cls=ScoutBot)
            scout_bot.ai_handler.evaluate_batch.return_value = {
                "https://www.linkedin.com/jobs/view/9000000401/": {"score": 88, "reason": "Strong leadership fit"}
            }

            with patch("app.bot.scout_bot.human_sleep"), patch("app.bot.scout_bot.simulate_reading"), patch(
                "app.bot.bot.smart_click"
            ):
                scout_bot.apply_jobs("United States")

            scout_df = scout_bot.db.get_scout_jobs()
            self.assertEqual(len(scout_df), 1)
            self.assertEqual(scout_df.iloc[0]["title"], "VP Finance")
            self.assertEqual(
                scout_bot.db.get_scout_status("https://www.linkedin.com/jobs/view/9000000401/", job_id="9000000401"),
                ("Scouted-Match", "Strong leadership fit"),
            )

    def test_get_job_cards_prefers_refreshed_even_when_shorter_and_handles_stale_scroll(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            card1 = _build_sdui_card("9000000451", "Engineer One", "Corp A")
            card2 = _build_sdui_card("9000000452", "Engineer Two", "Corp B")
            fresh_card = _build_sdui_card("9000000453", "Engineer Three", "Corp C")

            browser = FakeBrowser()
            bot = _make_bot(browser, tmp_dir)

            with patch.object(bot, "_find_job_cards_now", side_effect=[[card1, card2], [fresh_card]]), patch("time.sleep"):
                cards = bot._get_job_cards(timeout=0.1)
            self.assertEqual(cards, [fresh_card])

            with patch.object(bot, "_find_job_cards_now", side_effect=[[card1], [fresh_card]]), patch.object(
                browser, "execute_script", side_effect=StaleElementReferenceException("detached")
            ), patch("time.sleep"):
                cards_after_stale = bot._get_job_cards(timeout=0.1)
            self.assertEqual(cards_after_stale, [fresh_card])

    def test_apply_jobs_retries_on_stale_element_during_prescan(self):
        class StaleCard(FakeElement):
            def get_attribute(self, name):
                raise StaleElementReferenceException("stale card during prescan")

        for cls, sleep_mod, read_mod in (
            (LinkedinEasyApply, "app.bot.bot.human_sleep", "app.bot.bot.simulate_reading"),
            (ScoutBot, "app.bot.scout_bot.human_sleep", "app.bot.scout_bot.simulate_reading"),
        ):
            with tempfile.TemporaryDirectory() as tmp_dir:
                stale_card = StaleCard(tag="div", attrs={"componentkey": "job-card-component-ref-9000000500"})
                good_card = _build_sdui_card("9000000501", "Staff Engineer", "Acme")
                details = FakeElement(
                    tag="div",
                    attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                    text="About the job\nBuild reliable distributed systems",
                    children=[FakeElement(tag="button", text="Easy Apply", attrs={"aria-label": "Easy Apply"})],
                )
                bot = _make_bot(FakeBrowser([good_card, details]), tmp_dir, cls=cls)
                bot.ai_handler.evaluate_batch.return_value = {
                    "https://www.linkedin.com/jobs/view/9000000501/": {"score": 40, "reason": "Too junior"}
                }

                with patch.object(bot, "_get_job_cards", side_effect=[[stale_card], [good_card]]) as mock_get_cards, patch(
                    "time.sleep"
                ), patch(sleep_mod), patch(read_mod), patch("app.bot.bot.smart_click"):
                    bot.apply_jobs("United States")

                self.assertEqual(mock_get_cards.call_count, 2)
                bot.ai_handler.evaluate_batch.assert_called_once()

    def test_apply_jobs_reloads_list_on_stale_element_mid_loop(self):
        for cls, sleep_mod, read_mod in (
            (LinkedinEasyApply, "app.bot.bot.human_sleep", "app.bot.bot.simulate_reading"),
            (ScoutBot, "app.bot.scout_bot.human_sleep", "app.bot.scout_bot.simulate_reading"),
        ):
            with tempfile.TemporaryDirectory() as tmp_dir:
                card1 = _build_sdui_card("9000000601", "Backend Engineer", "Alpha")
                stale_card2 = _build_sdui_card("9000000602", "Platform Engineer", "Beta")
                fresh_card1 = _build_sdui_card("9000000601", "Backend Engineer", "Alpha")
                fresh_card2 = _build_sdui_card("9000000602", "Platform Engineer", "Beta")
                details = FakeElement(
                    tag="div",
                    attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                    text="About the job\nBackend and platform engineering role",
                    children=[FakeElement(tag="button", text="Easy Apply", attrs={"aria-label": "Easy Apply"})],
                )
                bot = _make_bot(FakeBrowser([fresh_card1, fresh_card2, details]), tmp_dir, cls=cls)
                bot.ai_handler.evaluate_batch.return_value = {
                    "https://www.linkedin.com/jobs/view/9000000601/": {"score": 50, "reason": "Low match"},
                    "https://www.linkedin.com/jobs/view/9000000602/": {"score": 50, "reason": "Low match"},
                }

                clicked_tiles = []

                def click_side_effect(tile):
                    if tile is stale_card2:
                        raise StaleElementReferenceException("stale mid-loop")
                    clicked_tiles.append(tile)

                with patch.object(
                    bot, "_get_job_cards", side_effect=[[card1, stale_card2], [fresh_card1, fresh_card2]]
                ) as mock_get_cards, patch.object(
                    bot, "_click_job_card", side_effect=click_side_effect
                ), patch("time.sleep"), patch(sleep_mod), patch(read_mod):
                    bot.apply_jobs("United States")

                self.assertEqual(mock_get_cards.call_count, 2)
                self.assertEqual(clicked_tiles, [card1, fresh_card2])
                bot.ai_handler.evaluate_batch.assert_called_once()
                evaluated_batch = bot.ai_handler.evaluate_batch.call_args[0][0]
                self.assertEqual(len(evaluated_batch), 2)

    def test_check_job_eligibility_waits_for_slow_easy_apply_button(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pane_loading = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="Loading job details...",
            )
            easy_btn = FakeElement(
                tag="button",
                text="Easy Apply",
                attrs={"aria-label": "Easy Apply to this job"},
            )
            bot = _make_bot(FakeBrowser([pane_loading]), tmp_dir)

            with patch.object(bot, "_find_easy_apply_button", side_effect=[None, None, easy_btn]), patch(
                "time.sleep"
            ) as mock_sleep:
                self.assertEqual(bot.check_job_eligibility(timeout=10.0), "Ready")
            self.assertEqual(mock_sleep.call_count, 3)
            for call in mock_sleep.call_args_list:
                self.assertGreaterEqual(call.args[0], 0.35)
                self.assertLessEqual(call.args[0], 1.55)

            pane_ready = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                children=[easy_btn],
            )
            bot_fast = _make_bot(FakeBrowser([pane_ready]), tmp_dir)
            with patch("time.sleep") as mock_fast_sleep:
                self.assertEqual(bot_fast.check_job_eligibility(timeout=10.0), "Ready")
            mock_fast_sleep.assert_not_called()

    def test_check_job_eligibility_external_and_closed_exit_without_waiting(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pane_ext = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="Example Corp\nApply\nSave",
                children=[FakeElement(tag="a", text="Apply", attrs={"aria-label": "Apply on company website"})],
            )
            bot_ext = _make_bot(FakeBrowser([pane_ext]), tmp_dir)
            with patch("time.sleep") as mock_sleep:
                self.assertEqual(bot_ext.check_job_eligibility(timeout=10.0), "Not Easy Apply (Button missing)")
            mock_sleep.assert_not_called()

            pane_closed = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="Senior Engineer\nNo longer accepting applications",
            )
            bot_closed = _make_bot(FakeBrowser([pane_closed]), tmp_dir)
            with patch("time.sleep") as mock_sleep:
                self.assertEqual(
                    bot_closed.check_job_eligibility(timeout=10.0),
                    "Not Easy Apply (No longer accepting applications)",
                )
            mock_sleep.assert_not_called()

    def test_check_job_eligibility_timeout_is_transient_in_apply_jobs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pane_empty = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="",
            )
            bot = _make_bot(FakeBrowser([pane_empty]), tmp_dir)
            ticks = iter([0.0, 2.0, 4.0, 8.0])
            with patch("app.bot.bot.time.time", side_effect=lambda: next(ticks, 10.0)), patch("time.sleep"):
                self.assertEqual(bot.check_job_eligibility(timeout=5.0), "Loading Timeout (Retry later)")

            card = _build_sdui_card("9000000701", "Data Manager", "SlowCorp")
            bot_loop = _make_bot(FakeBrowser([card, pane_empty]), tmp_dir)
            with patch.object(bot_loop, "check_job_eligibility", return_value="Loading Timeout (Retry later)"), patch(
                "app.bot.bot.human_sleep"
            ), patch("app.bot.bot.smart_click"):
                bot_loop.apply_jobs("United States")

            self.assertIsNone(
                bot_loop.db.get_job_status("https://www.linkedin.com/jobs/view/9000000701/", job_id="9000000701")
            )

    def test_find_easy_apply_button_supports_anchor_text_and_sdui_href(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            anchor_text = FakeElement(tag="a", text="Easy Apply")
            pane_text = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                children=[anchor_text],
            )
            bot_text = _make_bot(FakeBrowser([pane_text]), tmp_dir)
            self.assertIs(bot_text._find_easy_apply_button(), anchor_text)

            anchor_href = FakeElement(
                tag="a",
                text="Apply",
                attrs={"href": "https://www.linkedin.com/jobs/view/12345/apply/?openSDUIApplyFlow=true"},
            )
            pane_href = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                children=[anchor_href],
            )
            bot_href = _make_bot(FakeBrowser([pane_href]), tmp_dir)
            self.assertIs(bot_href._find_easy_apply_button(), anchor_href)

    def test_apply_to_job_waits_for_slow_button_and_retries_on_timeout(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            pane_loading = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="",
            )
            easy_btn = FakeElement(tag="button", text="Easy Apply", attrs={"aria-label": "Easy Apply to this job"})
            bot = _make_bot(FakeBrowser([pane_loading]), tmp_dir)
            bot.form = MagicMock()
            bot.form.find_primary_button.return_value = None

            with patch.object(bot, "_find_easy_apply_button", side_effect=[None, easy_btn]), patch(
                "app.bot.bot.smart_click"
            ) as mock_click, patch("app.bot.bot.human_sleep"), patch("time.sleep"):
                bot.apply_to_job()
            mock_click.assert_called_once_with(bot.browser, easy_btn)

            bot_timeout = _make_bot(FakeBrowser([pane_loading]), tmp_dir)
            ticks = iter([0.0, 2.0, 4.0, 8.0, 12.0])
            with patch("app.bot.bot.time.time", side_effect=lambda: next(ticks, 15.0)), patch("time.sleep"):
                self.assertEqual(bot_timeout.apply_to_job(), ("Retry Later", "Easy Apply button load timeout"))

            with patch.object(bot_timeout, "_click_job_card"), patch("app.bot.bot.human_sleep"), patch.object(
                bot_timeout, "apply_to_job", return_value=("Retry Later", "Easy Apply button load timeout")
            ):
                bot_timeout.ai_handler.evaluate_batch.return_value = {
                    "https://www.linkedin.com/jobs/view/9000000702/": {"score": 90, "reason": "Strong fit"}
                }
                bot_timeout._process_apply_batch(
                    [
                        {
                            "id": "https://www.linkedin.com/jobs/view/9000000702/",
                            "job_id": "9000000702",
                            "text": "Job description",
                            "title": "Data Lead",
                            "company": "SlowCorp",
                            "index": 0,
                            "location": "United States",
                        }
                    ],
                    [_build_sdui_card("9000000702", "Data Lead", "SlowCorp")],
                )
            self.assertIsNone(
                bot_timeout.db.get_job_status("https://www.linkedin.com/jobs/view/9000000702/", job_id="9000000702")
            )

            pane_applied = FakeElement(
                tag="div",
                attrs={"data-sdui-screen": "com.linkedin.sdui.flagshipnav.jobs.SemanticJobDetails"},
                text="Applied 2 days ago",
            )
            bot_applied = _make_bot(FakeBrowser([pane_applied]), tmp_dir)
            ticks_applied = iter([0.0, 2.0, 4.0, 8.0, 12.0])
            with patch("app.bot.bot.time.time", side_effect=lambda: next(ticks_applied, 15.0)), patch("time.sleep"):
                self.assertEqual(bot_applied.apply_to_job(), ("Already Applied", "Button not found"))


if __name__ == "__main__":
    unittest.main()
