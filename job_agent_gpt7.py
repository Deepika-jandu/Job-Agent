"""
╔══════════════════════════════════════════════════════════════╗
║         AI Job Application Agent — Deepika Jandu             ║
║         Platforms: LinkedIn · Naukri · Wellfound             ║
╚══════════════════════════════════════════════════════════════╝

CAPTCHA FIX:
  Uses your real Chrome browser with your existing logged-in session.
  You only need to log in once manually — after that the agent reuses
  your saved cookies automatically.

SETUP:
  pip install playwright openai python-dotenv rich
  playwright install chromium
  cp .env.example .env

FIRST RUN (log in once):
  python job_agent_gpt.py --setup

NORMAL RUN:
  python job_agent_gpt.py
"""

import os
import re
import csv
import json
import time
import asyncio
import sqlite3
import argparse
from urllib.parse import quote_plus
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import List, Tuple

from openai import OpenAI
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, BrowserContext, TimeoutError as PlaywrightTimeoutError
from rich.console import Console
from rich.table import Table

load_dotenv()
console = Console()

# ─────────────────────────────────────────────
#  WINDOWS CHROME PATH — auto-detected
# ─────────────────────────────────────────────

CHROME_PATHS_WINDOWS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

def find_chrome() -> str:
    for path in CHROME_PATHS_WINDOWS:
        if os.path.exists(path):
            return path
    raise FileNotFoundError(
        "Chrome not found. Install Chrome or set CHROME_PATH in your .env file."
    )

CHROME_EXECUTABLE = os.getenv("CHROME_PATH") or find_chrome()

# Persistent profile — stores your cookies/session between runs
USER_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile")

# ─────────────────────────────────────────────
# -------------------------------------------------
#  BROWSER SETTINGS
# -------------------------------------------------

BROWSER_ARGS = [
    "--start-maximized",
    "--disable-popup-blocking",
    "--no-first-run",
    "--no-default-browser-check",
]

MANUAL_LOGIN_WAIT_SECONDS = int(os.getenv("MANUAL_LOGIN_WAIT_SECONDS", "900"))

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

CONFIG = {
    "match_threshold": 75,
    "max_applications_per_run": 20,
    "action_delay_min": 3,
    "action_delay_max": 6,

    "search_queries": [
        "Data Analyst",
        "Data Scientist",
        "ML Engineer",
        "Analytics Engineer",
        "Machine Learning Engineer",
        "Data Analyst Remote",
        "Data Scientist Remote",
        "ML Engineer Remote",
        "Analytics Engineer Remote",
        "Machine Learning Engineer Remote",
        "Data Analyst India",
        "Data Scientist India",
        "ML Engineer India",
    ],

    "location": "India",

    "platforms": {
        "linkedin":  True,
        "naukri":    True,
        "wellfound": True,
    },

    "profile_summary": """
Name: Deepika Jandu
Education: IIT Bombay, B.Tech (2021 to 2025), 10 SPI in sixth semester
JEE: Top 2.8% Advanced, top 2.3% Main

Current Role: Data Analyst at AxionRay (Nov 2025 to Present)
- Scalable analytics & feature pipelines over 1M+ manufacturing records/month
- Anomaly detection, clustering, supervised classification
- Cross-functional collaboration with product & engineering

Previous: Data Science Intern at Olyv/SmartCoin (May to Sept 2025)
- Processed 3B+ transactions, merchant classification (3% lift over RegX)
- BigQuery analysis, 300+ engineered features, 150+ optimized batch queries

Research Intern: NCAIR Lab IIT Bombay
- IoT lift monitoring, XGBoost fault detection (92% accuracy)
- Reduced manual inspection by 40%

Skills:
- ML: XGBoost, LSTM, Random Forest, SVM, ARIMA, K-Means, NLTK
- DL: CNN, RNN, TensorFlow, PyTorch, Keras, VAE, LangChain, LangGraph, RAG
- Analytics: Pandas, NumPy, Matplotlib, Seaborn, Plotly, Tableau, Power BI, BigQuery
- Languages: Python, SQL, PHP, HTML, CSS
- Tools: Figma, Excel, After Effects, Illustrator
""",
}

# ─────────────────────────────────────────────
#  DATA MODELS
# ─────────────────────────────────────────────

@dataclass
class Job:
    id: str
    title: str
    company: str
    location: str
    platform: str
    url: str
    description: str
    posted_date: str
    match_score: int = 0
    match_reason: str = ""
    applied: bool = False
    applied_at: str = ""
    status: str = "found"


# ─────────────────────────────────────────────
#  DATABASE
# ─────────────────────────────────────────────

class JobDB:
    def __init__(self, path: str = "jobs.db"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                title TEXT, company TEXT, location TEXT,
                platform TEXT, url TEXT, description TEXT,
                posted_date TEXT, match_score INTEGER,
                match_reason TEXT, applied INTEGER DEFAULT 0,
                applied_at TEXT, status TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self.conn.commit()

    def upsert(self, job: Job):
        d = asdict(job)
        d["applied"] = int(d["applied"])
        cols         = ", ".join(d.keys())
        placeholders = ", ".join(["?"] * len(d))
        updates      = ", ".join(f"{k}=excluded.{k}" for k in d if k != "id")
        self.conn.execute(
            f"INSERT INTO jobs ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            list(d.values())
        )
        self.conn.commit()

    def already_applied(self, job_id: str) -> bool:
        row = self.conn.execute(
            "SELECT applied FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        return bool(row and row["applied"])

    def get_all(self) -> List[Job]:
        rows = self.conn.execute(
            "SELECT * FROM jobs ORDER BY match_score DESC"
        ).fetchall()
        return [Job(**dict(r)) for r in rows]

    def export_csv(self, path: str = "applications.csv"):
        jobs = self.get_all()
        if not jobs:
            return
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=asdict(jobs[0]).keys())
            w.writeheader()
            w.writerows([asdict(j) for j in jobs])
        console.log(f"[green]Exported {len(jobs)} jobs to {path}[/green]")


# ─────────────────────────────────────────────
#  AI SCORER
# ─────────────────────────────────────────────

class JobScorer:
    def __init__(self):
        self.client = OpenAI(api_key="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")

    def score(self, job: Job) -> Tuple[int, str]:
        prompt = f"""
You are a job-matching expert. Score how well this job fits the candidate's profile.

CANDIDATE PROFILE:
{CONFIG['profile_summary']}

JOB TITLE: {job.title}
COMPANY: {job.company}
DESCRIPTION:
{job.description[:2000]}

Return JSON only, no explanation outside JSON:
{{
  "score": <integer 0-100>,
  "reason": "<one sentence explaining the match>",
  "matched_skills": ["skill1", "skill2"],
  "gaps": ["gap1"]
}}
"""
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.2,
            )
            raw  = response.choices[0].message.content.strip()
            raw  = re.sub(r"```json|```", "", raw).strip()
            data = json.loads(raw)
            return int(data.get("score", 0)), data.get("reason", "")
        except Exception as e:
            console.log(f"[yellow]Scorer error: {e}[/yellow]")
            return 0, "Scoring failed"

    def generate_cover_note(self, job: Job) -> str:
        prompt = f"""
Write a concise, professional cover note (3-4 sentences max) for this job application.
Tailor it to the specific role. Be direct and highlight most relevant experience.

CANDIDATE: Deepika Jandu (IIT Bombay, Data Analyst at AxionRay)
JOB: {job.title} at {job.company}
DESCRIPTION SNIPPET: {job.description[:500]}

Return only the cover note text, no subject line, no greeting needed.
"""
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200,
                temperature=0.5,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            console.log(f"[yellow]Cover note error: {e}[/yellow]")
            return (
                "Experienced Data Analyst from IIT Bombay with expertise in ML pipelines, "
                "BigQuery, and large-scale analytics. Excited to bring my experience in "
                "anomaly detection, feature engineering, and cross-functional collaboration "
                "to this role."
            )


# ─────────────────────────────────────────────
#  PLATFORM SCRAPERS
# ─────────────────────────────────────────────
class BaseScraper:
    name = "base"
    login_url = ""
    check_url = ""

    def __init__(self, page: Page):
        self.page = page

    async def safe_goto(self, url: str, wait_until: str = "domcontentloaded"):
        await self.page.goto(url, wait_until=wait_until, timeout=60000)
        try:
            await self.page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeoutError:
            pass

    async def wait_for_manual_login(self, platform_name: str) -> bool:
        while True:
            console.print(
                f"\n[bold yellow]{platform_name}: complete login manually in the opened Chrome window.[/bold yellow]\n"
                "Finish password/OTP/CAPTCHA/2FA if shown. Do not close Chrome.\n"
                "Only press Enter here after you can clearly see that you are logged in.\n"
            )
            await asyncio.get_event_loop().run_in_executor(
                None, input, f"  -> Press Enter after {platform_name} login is complete: "
            )
            await asyncio.sleep(5)
            if await self.is_logged_in():
                console.log(f"[green]{platform_name}: login verified and session saved[/green]")
                return True
            console.print(
                f"[yellow]{platform_name}: I still could not verify login.[/yellow]\n"
                "If the browser is still on OTP/CAPTCHA/login, finish it and press Enter again.\n"
                "Type 's' then Enter to skip this platform.\n"
            )
            choice = await asyncio.get_event_loop().run_in_executor(
                None, input, "  -> Press Enter to retry verification, or type s to skip: "
            )
            if choice.strip().lower() == "s":
                return False

    async def manual_login(self) -> bool:
        if not self.login_url:
            return False
        await self.safe_goto(self.login_url)
        return await self.wait_for_manual_login(self.name.title())

    async def human_delay(self):
        delay = CONFIG["action_delay_min"] + (
            (CONFIG["action_delay_max"] - CONFIG["action_delay_min"]) *
            (time.time() % 1)
        )
        await asyncio.sleep(delay)

    async def is_logged_in(self) -> bool:             raise NotImplementedError
    async def search_jobs(self, q, loc) -> List[Job]: raise NotImplementedError
    async def get_description(self, job: Job) -> str: raise NotImplementedError
    async def apply(self, job: Job, note: str) -> bool: raise NotImplementedError


class LinkedInScraper(BaseScraper):
    name = "linkedin"
    login_url = "https://www.linkedin.com/login"
    check_url = "https://www.linkedin.com/feed/"

    async def is_logged_in(self) -> bool:
        try:
            await self.safe_goto(self.check_url)
            if any(x in self.page.url for x in ["/feed", "/mynetwork", "/jobs"]):
                return True
            selectors = [
                "a[href*='/in/']",
                "button[aria-label*='Me']",
                "img.global-nav__me-photo",
                ".global-nav__me",
            ]
            for sel in selectors:
                if await self.page.query_selector(sel):
                    return True
        except Exception as e:
            console.log(f"[yellow]LinkedIn login check warning: {e}[/yellow]")
        return False

    async def login(self) -> bool:
        return await self.manual_login()

    async def search_jobs(self, query: str, location: str) -> List[Job]:
        jobs = []
        url = (
            "https://www.linkedin.com/jobs/search/"
            f"?keywords={quote_plus(query)}"
            f"&location={quote_plus(location)}"
            "&f_AL=true&sortBy=DD"
        )

        try:
            await self.safe_goto(url, wait_until="domcontentloaded")
        except PlaywrightTimeoutError:
            console.log("[yellow]LinkedIn: page load timed out, continuing with visible content[/yellow]")

        card_selectors = [
            ".job-card-container",
            "li.jobs-search-results__list-item",
            ".jobs-search-two-pane__job-card-container",
            ".job-search-card",
        ]

        found_cards = False
        for sel in card_selectors:
            try:
                await self.page.wait_for_selector(sel, timeout=12000)
                found_cards = True
                break
            except PlaywrightTimeoutError:
                continue

        if not found_cards:
            console.log(
                f"[yellow]LinkedIn: no job cards found for {query}.[/yellow]"
            )
            await self.page.screenshot(path="debug_linkedin_search.png", full_page=True)
            return []

        for _ in range(3):
            await self.page.mouse.wheel(0, 900)
            await asyncio.sleep(1)

        seen_urls = set()
        for card_sel in card_selectors:
            for card in await self.page.query_selector_all(card_sel):
                try:
                    link_el = await card.query_selector('a[href*="/jobs/view/"]')
                    href = await link_el.get_attribute("href") if link_el else ""
                    if not href:
                        continue
                    href = href.split("?")[0]
                    if href.startswith("/"):
                        href = "https://www.linkedin.com" + href
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)

                    title = ""
                    for sel in [
                        ".job-card-list__title",
                        ".job-card-list__title--link",
                        ".job-search-card__title",
                        "strong",
                        'a[href*="/jobs/view/"]',
                    ]:
                        el = await card.query_selector(sel)
                        if el:
                            title = (await el.inner_text()).strip()
                            if title:
                                break

                    company = ""
                    for sel in [
                        ".job-card-container__primary-description",
                        ".artdeco-entity-lockup__subtitle",
                        ".job-search-card__company-name",
                    ]:
                        el = await card.query_selector(sel)
                        if el:
                            company = (await el.inner_text()).strip()
                            if company:
                                break

                    loc = ""
                    for sel in [
                        ".job-card-container__metadata-item",
                        ".job-search-card__location",
                        ".artdeco-entity-lockup__caption",
                    ]:
                        el = await card.query_selector(sel)
                        if el:
                            loc = (await el.inner_text()).strip()
                            if loc:
                                break

                    if not title:
                        continue

                    job_id_match = re.search(r"/jobs/view/(\d+)", href)
                    job_id = job_id_match.group(1) if job_id_match else str(abs(hash(href)) % 10**12)

                    jobs.append(Job(
                        id=f"li_{job_id}",
                        title=title,
                        company=company,
                        location=loc or location,
                        platform="LinkedIn",
                        url=href,
                        description="",
                        posted_date=datetime.now().strftime("%Y-%m-%d"),
                    ))
                except Exception as e:
                    console.log(f"[dim]LinkedIn: skipped one card: {e}[/dim]")
                    continue

        return jobs[:15]

    async def get_description(self, job: Job) -> str:
        try:
            await self.safe_goto(job.url, wait_until="domcontentloaded")
            for sel in [
                ".jobs-description",
                ".jobs-box__html-content",
                "#job-details",
                ".description__text",
            ]:
                try:
                    await self.page.wait_for_selector(sel, timeout=8000)
                    el = await self.page.query_selector(sel)
                    if el:
                        text = (await el.inner_text()).strip()
                        if text:
                            return text[:3000]
                except PlaywrightTimeoutError:
                    continue
        except Exception as e:
            console.log(f"[yellow]LinkedIn description warning: {e}[/yellow]")
        return ""

    async def find_linkedin_easy_apply_button(self):
        """
        Finds the Easy Apply button using a JS-based DOM scan.

        This is the most reliable method regardless of LinkedIn layout changes —
        it scans ALL buttons in the DOM for text/aria containing 'easy apply',
        checks visibility via bounding box, and returns the first match.

        Falls back to a broader scan of primary-styled buttons if no explicit
        'easy apply' label is found (handles cases where button text is rendered
        via inner span or icon-only with aria-label).
        """
        await asyncio.sleep(2)

        # Scroll the job detail pane into view so lazy-rendered buttons appear
        scroll_targets = [
            ".jobs-unified-top-card",
            ".job-details-jobs-unified-top-card__container--two-pane",
            ".jobs-details__main-content",
            ".scaffold-layout__detail",
            "main",
        ]
        for sel in scroll_targets:
            try:
                el = await self.page.query_selector(sel)
                if el:
                    await el.scroll_into_view_if_needed()
                    await asyncio.sleep(0.5)
                    break
            except Exception:
                continue

        # ── PASS 1: JS full-DOM scan for any button whose text or aria-label
        #            contains "easy apply" (case-insensitive).
        #            Returns the first visible, enabled one.
        easy_apply_btn = await self.page.evaluate_handle("""
            () => {
                const buttons = Array.from(document.querySelectorAll('button'));
                for (const btn of buttons) {
                    const text = (btn.innerText || btn.textContent || '').toLowerCase().trim();
                    const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                    if (!text.includes('easy apply') && !aria.includes('easy apply')) continue;
                    // Check it's actually visible
                    const rect = btn.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    if (btn.disabled) continue;
                    return btn;
                }
                return null;
            }
        """)

        try:
            # evaluate_handle returns a JSHandle; check if it resolved to a real element
            is_null = await self.page.evaluate("el => el === null", easy_apply_btn)
            if not is_null:
                loc = self.page.locator("button").filter(has=self.page.locator(":scope"))
                # Wrap the JS handle as a Playwright locator using the element itself
                element_loc = self.page.locator(
                    "xpath=//button[contains(translate(normalize-space(.), "
                    "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'easy apply')]"
                    " | //button[contains(translate(@aria-label, "
                    "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'easy apply')]"
                )
                count = await element_loc.count()
                for i in range(count):
                    item = element_loc.nth(i)
                    try:
                        if await item.is_visible() and await item.is_enabled():
                            console.log("[green]LinkedIn: Easy Apply button found via XPath scan[/green]")
                            return item
                    except Exception:
                        continue
        except Exception:
            pass

        # ── PASS 2: Broad CSS selector sweep (covers span-wrapped text & icon buttons)
        css_candidates = [
            # ✅ Exact match from inspected DOM: span.artdeco-button__text containing "Easy Apply"
            "span.artdeco-button__text",  # will filter by text in loop below
            # Current 2024-2025 LinkedIn selectors
            "button.jobs-apply-button",
            "button[class*='jobs-apply-button']",
            ".jobs-apply-button",
            ".jobs-s-apply button",
            ".jobs-s-apply__button",
            # Artdeco primary buttons that contain Easy Apply text
            ".artdeco-button--primary:has-text('Easy Apply')",
            "button.artdeco-button--primary:has-text('Easy Apply')",
            # Span-inside-button pattern
            "button:has(span.artdeco-button__text)",
            "button:has(span:text-is('Easy Apply'))",
            "button:has(span:text-matches('Easy Apply', 'i'))",
            # aria-label based
            "button[aria-label*='Easy Apply' i]",
            "button[aria-label*='easy apply' i]",
        ]
        # Special handling for span.artdeco-button__text — find the span, check text, return parent button
        try:
            spans = self.page.locator("span.artdeco-button__text")
            span_count = await spans.count()
            for i in range(span_count):
                span = spans.nth(i)
                try:
                    text = (await span.inner_text()).strip().lower()
                    if "easy apply" not in text:
                        continue
                    if not await span.is_visible():
                        continue
                    # Walk up to the parent button
                    btn = span.locator("xpath=ancestor::button[1]")
                    if await btn.count() and await btn.first.is_visible() and await btn.first.is_enabled():
                        console.log("[green]LinkedIn: Easy Apply button found via span.artdeco-button__text[/green]")
                        return btn.first
                except Exception:
                    continue
        except Exception:
            pass
        for sel in css_candidates:
            try:
                loc = self.page.locator(sel)
                count = await loc.count()
                for i in range(count):
                    item = loc.nth(i)
                    if await item.is_visible() and await item.is_enabled():
                        console.log(f"[green]LinkedIn: Easy Apply button found via CSS: {sel}[/green]")
                        return item
            except Exception:
                continue

        # ── PASS 3: Inspect every visible button's full text via Playwright
        #            (catches dynamic/shadow text that CSS misses)
        try:
            all_buttons = self.page.locator("button")
            count = await all_buttons.count()
            for i in range(count):
                btn = all_buttons.nth(i)
                try:
                    if not await btn.is_visible():
                        continue
                    text = (await btn.inner_text()).strip().lower()
                    aria = ((await btn.get_attribute("aria-label")) or "").lower()
                    if "easy apply" in text or "easy apply" in aria:
                        if await btn.is_enabled():
                            console.log("[green]LinkedIn: Easy Apply button found via full button scan[/green]")
                            return btn
                except Exception:
                    continue
        except Exception:
            pass

        # ── PASS 4: Wait 3 more seconds and retry Pass 3 once
        #            (handles slow page renders / lazy hydration)
        console.log("[yellow]LinkedIn: Easy Apply not found yet — waiting 3s and retrying...[/yellow]")
        await asyncio.sleep(3)
        try:
            all_buttons = self.page.locator("button")
            count = await all_buttons.count()
            for i in range(count):
                btn = all_buttons.nth(i)
                try:
                    if not await btn.is_visible():
                        continue
                    text = (await btn.inner_text()).strip().lower()
                    aria = ((await btn.get_attribute("aria-label")) or "").lower()
                    if "easy apply" in text or "easy apply" in aria:
                        if await btn.is_enabled():
                            console.log("[green]LinkedIn: Easy Apply button found on retry scan[/green]")
                            return btn
                except Exception:
                    continue
        except Exception:
            pass

        return None

    async def debug_linkedin_buttons(self, job: Job):
        try:
            await self.page.screenshot(path="debug_linkedin_apply.png", full_page=True)
        except Exception:
            pass

        labels = []
        try:
            buttons = self.page.locator("button")
            count = min(await buttons.count(), 60)
            for i in range(count):
                b = buttons.nth(i)
                try:
                    txt = (await b.inner_text()).strip().replace("\n", " ")
                    aria = await b.get_attribute("aria-label")
                    cls = (await b.get_attribute("class")) or ""
                    label = aria or txt
                    if label:
                        labels.append(f"{label[:80]} [cls={cls[:40]}]")
                except Exception:
                    continue
        except Exception:
            pass

        console.log(f"[yellow]LinkedIn: no Easy Apply button detected for {job.title}[/yellow]")
        if labels:
            console.log("[yellow]All visible LinkedIn buttons:[/yellow]")
            for l in labels[:20]:
                console.log(f"  [dim]{l}[/dim]")
        console.log("[yellow]Saved screenshot: debug_linkedin_apply.png[/yellow]")

        # Also dump page HTML snippet around apply area for diagnosis
        try:
            snippet = await self.page.evaluate("""
                () => {
                    const el = document.querySelector('.jobs-unified-top-card') 
                               || document.querySelector('.jobs-s-apply')
                               || document.querySelector('main');
                    return el ? el.innerHTML.substring(0, 3000) : 'top-card not found';
                }
            """)
            with open("debug_linkedin_html.txt", "w", encoding="utf-8") as f:
                f.write(snippet)
            console.log("[yellow]Saved HTML snippet: debug_linkedin_html.txt[/yellow]")
        except Exception:
            pass

    async def apply(self, job: Job, cover_note: str) -> bool:
        try:
            await self.safe_goto(job.url, wait_until="domcontentloaded")
            # Extra wait — LinkedIn lazy-renders the apply button after the page loads
            await asyncio.sleep(5)

            current_url = self.page.url.lower()
            if "checkpoint" in current_url or "/login" in current_url:
                console.log(f"[yellow]LinkedIn: checkpoint/login page for {job.title}; complete it manually.[/yellow]")
                await self.debug_linkedin_buttons(job)
                return False

            btn = await self.find_linkedin_easy_apply_button()

            # One reload retry if button not found yet
            if not btn:
                console.log("[yellow]LinkedIn: retrying after page reload...[/yellow]")
                await self.page.reload(wait_until="domcontentloaded")
                await asyncio.sleep(5)
                btn = await self.find_linkedin_easy_apply_button()

            if not btn:
                await self.debug_linkedin_buttons(job)
                return False

            await btn.scroll_into_view_if_needed()
            await asyncio.sleep(0.5)
            await btn.click(timeout=10000)
            await self.human_delay()

            for step in range(20):
                await asyncio.sleep(1.5)
                await self._fill_easy_apply_form(cover_note)

                submit  = self.page.get_by_role("button", name=re.compile(r"submit application", re.I))
                review  = self.page.get_by_role("button", name=re.compile(r"review", re.I))
                next_btn = self.page.get_by_role("button", name=re.compile(r"next|continue", re.I))

                if await submit.count() and await submit.first.is_visible():
                    await submit.first.click()
                    await self.human_delay()
                    console.log(f"[green]LinkedIn: Submitted Easy Apply for {job.title} @ {job.company}[/green]")
                    return True

                if await review.count() and await review.first.is_visible() and await review.first.is_enabled():
                    await review.first.click()
                    await self.human_delay()
                    continue

                if await next_btn.count() and await next_btn.first.is_visible() and await next_btn.first.is_enabled():
                    await next_btn.first.click()
                    await self.human_delay()
                    continue

                modal = await self.page.query_selector(".jobs-easy-apply-modal, div[role='dialog']")
                if not modal:
                    console.log(f"[green]LinkedIn: Easy Apply completed for {job.title} @ {job.company}[/green]")
                    return True

                await asyncio.sleep(1)

            console.log(f"[yellow]LinkedIn: Easy Apply did not complete for {job.title}[/yellow]")
            return False
        except Exception as e:
            console.log(f"[red]LinkedIn apply error: {e}[/red]")
            try:
                await self.page.screenshot(path="debug_linkedin_apply_error.png", full_page=True)
                console.log("[yellow]Saved screenshot: debug_linkedin_apply_error.png[/yellow]")
            except Exception:
                pass
        return False

    async def _fill_easy_apply_form(self, cover_note: str):
        """
        Auto-fills all common LinkedIn Easy Apply form fields.
        Answers used:
          - Years of experience  → inferred from question label (default 4)
          - Expected salary      → 1200000 (12 LPA)
          - Current salary       → 800000  (8 LPA)
          - Notice period        → 30
          - Yes/No experience    → Yes
          - Phone                → from PHONE_NUMBER env var
          - Cover letter         → generated cover note
          - City / location      → Bangalore (or from env CITY)
          - LinkedIn profile URL → from env LINKEDIN_URL
        """
        page = self.page

        # ── Keywords that tell us what a field is asking ─────────────────────
        EXPERIENCE_KW  = ["year", "experience", "exp"]
        EXPECTED_SAL_KW = ["expected salary", "expected ctc", "expected compensation",
                           "desired salary", "salary expectation"]
        CURRENT_SAL_KW  = ["current salary", "current ctc", "current compensation",
                           "present salary"]
        NOTICE_KW       = ["notice period", "notice", "joining period", "days to join"]
        YES_NO_KW       = ["do you have experience", "have you worked", "are you familiar",
                           "do you know", "have experience", "experience with",
                           "proficient in", "worked with", "knowledge of"]
        CITY_KW         = ["city", "location", "current location", "preferred location"]
        LINKEDIN_KW     = ["linkedin", "linkedin url", "linkedin profile"]
        WEBSITE_KW      = ["website", "portfolio", "github", "personal url"]

        def label_matches(label_text: str, keywords: list) -> bool:
            lt = label_text.lower()
            return any(kw in lt for kw in keywords)

        # ── Helper: get the label text associated with a form element ─────────
        async def get_label(el) -> str:
            try:
                el_id = await el.get_attribute("id")
                aria  = await el.get_attribute("aria-label") or ""
                if aria:
                    return aria.lower()
                if el_id:
                    lbl = page.locator(f"label[for='{el_id}']")
                    if await lbl.count():
                        return (await lbl.first.inner_text()).lower()
                # Walk up to find a wrapping label or legend
                text = await el.evaluate("""el => {
                    let node = el;
                    for (let i = 0; i < 6; i++) {
                        node = node.parentElement;
                        if (!node) break;
                        const lbl = node.querySelector('label, legend, h3, .fb-dash-form-element__label');
                        if (lbl) return lbl.innerText;
                    }
                    return '';
                }""")
                return (text or "").lower()
            except Exception:
                return ""

        # ── 1. Text inputs & number inputs ───────────────────────────────────
        inputs = page.locator("input[type='text'], input[type='number'], input:not([type])")
        count  = await inputs.count()
        for i in range(count):
            inp = inputs.nth(i)
            try:
                if not await inp.is_visible():
                    continue
                existing = (await inp.input_value()).strip()
                label    = await get_label(inp)

                # Phone
                if any(k in label for k in ["phone", "mobile", "contact number"]):
                    if not existing and os.getenv("PHONE_NUMBER"):
                        await inp.fill(os.getenv("PHONE_NUMBER", ""))

                # LinkedIn URL
                elif label_matches(label, LINKEDIN_KW):
                    if not existing and os.getenv("LINKEDIN_URL"):
                        await inp.fill(os.getenv("LINKEDIN_URL", ""))

                # Website / portfolio
                elif label_matches(label, WEBSITE_KW):
                    if not existing and os.getenv("PORTFOLIO_URL"):
                        await inp.fill(os.getenv("PORTFOLIO_URL", ""))

                # City / location
                elif label_matches(label, CITY_KW):
                    if not existing:
                        await inp.fill(os.getenv("CITY", "Bangalore"))

                # Expected salary
                elif label_matches(label, EXPECTED_SAL_KW):
                    if not existing:
                        await inp.triple_click()
                        await inp.fill("1200000")

                # Current salary
                elif label_matches(label, CURRENT_SAL_KW):
                    if not existing:
                        await inp.triple_click()
                        await inp.fill("800000")

                # Notice period
                elif label_matches(label, NOTICE_KW):
                    if not existing:
                        await inp.triple_click()
                        await inp.fill("30")

                # Years of experience — any numeric input asking about years/exp
                elif label_matches(label, EXPERIENCE_KW):
                    if not existing:
                        # Try to infer from label (e.g. "years of Python experience")
                        await inp.triple_click()
                        await inp.fill("4")

            except Exception:
                continue

        # ── 2. Textareas (cover letter, additional info) ─────────────────────
        textareas = page.locator("textarea")
        count     = await textareas.count()
        for i in range(count):
            ta = textareas.nth(i)
            try:
                if not await ta.is_visible():
                    continue
                existing = (await ta.input_value()).strip()
                if not existing and cover_note:
                    await ta.fill(cover_note)
            except Exception:
                continue

        # ── 3. Select / dropdown fields ───────────────────────────────────────
        selects = page.locator("select")
        count   = await selects.count()
        for i in range(count):
            sel = selects.nth(i)
            try:
                if not await sel.is_visible():
                    continue
                label   = await get_label(sel)
                current = await sel.input_value()

                # Get all option texts to pick intelligently
                options = await sel.evaluate("""el => Array.from(el.options).map(o => ({
                    value: o.value, text: o.text.trim().toLowerCase()
                }))""")
                non_empty = [o for o in options if o["value"] and o["text"] not in ("select an option", "please select", "-", "")]

                if not non_empty:
                    continue

                def pick_option(keywords, prefer_yes=False):
                    if prefer_yes:
                        for o in non_empty:
                            if o["text"] in ("yes", "true", "1"):
                                return o["value"]
                    for kw in keywords:
                        for o in non_empty:
                            if kw in o["text"]:
                                return o["value"]
                    return non_empty[0]["value"]

                if not current or current == options[0]["value"]:
                    # Expected salary
                    if label_matches(label, EXPECTED_SAL_KW):
                        # Pick highest or closest to 12L
                        await sel.select_option(non_empty[-1]["value"])

                    # Current salary
                    elif label_matches(label, CURRENT_SAL_KW):
                        await sel.select_option(non_empty[0]["value"])

                    # Notice period — find option containing "30" or "1 month"
                    elif label_matches(label, NOTICE_KW):
                        v = pick_option(["30", "one month", "1 month", "30 days"])
                        await sel.select_option(v)

                    # Yes/No questions — always pick Yes
                    elif label_matches(label, YES_NO_KW) or label_matches(label, ["do you", "have you", "are you", "can you"]):
                        v = pick_option([], prefer_yes=True)
                        await sel.select_option(v)

                    # Years of experience
                    elif label_matches(label, EXPERIENCE_KW):
                        v = pick_option(["4", "3-5", "4-6", "3 to 5", "4 to 6", "2-4"])
                        await sel.select_option(v)

                    # Anything else with Yes/No options — default Yes
                    else:
                        yes_opt = next((o for o in non_empty if o["text"] in ("yes", "true")), None)
                        if yes_opt:
                            await sel.select_option(yes_opt["value"])
                        else:
                            await sel.select_option(non_empty[0]["value"])

            except Exception:
                continue

        # ── 4. Radio buttons ─────────────────────────────────────────────────
        radios = page.locator("input[type='radio']")
        count  = await radios.count()
        # Group by name attribute
        radio_groups: dict = {}
        for i in range(count):
            r = radios.nth(i)
            try:
                name = await r.get_attribute("name") or f"group_{i}"
                if name not in radio_groups:
                    radio_groups[name] = []
                radio_groups[name].append(r)
            except Exception:
                continue

        for name, group in radio_groups.items():
            try:
                # Check if any in group already selected
                any_checked = False
                for r in group:
                    if await r.is_checked():
                        any_checked = True
                        break
                if any_checked:
                    continue

                # Get label for the group from first visible radio
                group_label = ""
                for r in group:
                    group_label = await get_label(r)
                    if group_label:
                        break

                # Get value labels for each radio
                async def radio_label(r):
                    try:
                        rid = await r.get_attribute("id") or ""
                        val = await r.get_attribute("value") or ""
                        if rid:
                            lbl = page.locator(f"label[for='{rid}']")
                            if await lbl.count():
                                return (await lbl.first.inner_text()).strip().lower()
                        return val.lower()
                    except Exception:
                        return ""

                # Prefer Yes radio for yes/no groups
                yes_radio = None
                first_radio = None
                for r in group:
                    if not await r.is_visible():
                        continue
                    rl = await radio_label(r)
                    if first_radio is None:
                        first_radio = r
                    if rl in ("yes", "true", "1"):
                        yes_radio = r
                        break

                target = yes_radio or first_radio
                if target and await target.is_visible() and await target.is_enabled():
                    await target.click()

            except Exception:
                continue

        # ── 5. Checkboxes (e.g. "I agree to terms") ──────────────────────────
        checkboxes = page.locator("input[type='checkbox']")
        count      = await checkboxes.count()
        for i in range(count):
            cb = checkboxes.nth(i)
            try:
                if not await cb.is_visible():
                    continue
                if not await cb.is_checked():
                    label = await get_label(cb)
                    # Only auto-check agreement/consent checkboxes
                    if any(k in label for k in ["agree", "consent", "confirm", "acknowledge", "terms", "privacy"]):
                        await cb.click()
            except Exception:
                continue


class NaukriScraper(BaseScraper):
    name = "naukri"
    login_url = "https://www.naukri.com/nlogin/login"
    check_url = "https://www.naukri.com/mnjuser/homepage"

    async def is_logged_in(self) -> bool:
        try:
            await self.safe_goto(self.check_url)
            current = self.page.url.lower()
            if "login" in current or "nlogin" in current:
                return False
            logged_in_selectors = [
                "a[href*='mnjuser/profile']",
                "a[href*='profile']",
                "div[class*='user-name']",
                "div[class*='userName']",
                "img[alt*='profile' i]",
                "a:has-text('View profile')",
                "button:has-text('Logout')",
            ]
            for sel in logged_in_selectors:
                if await self.page.query_selector(sel):
                    return True
            return any(x in current for x in ["mnjuser/homepage", "mnjuser/profile", "myprofile"])
        except Exception as e:
            console.log(f"[yellow]Naukri login check warning: {e}[/yellow]")
        return False

    async def login(self) -> bool:
        return await self.manual_login()

    async def search_jobs(self, query: str, location: str) -> List[Job]:
        jobs = []
        url = (
            f"https://www.naukri.com/"
            f"{query.lower().replace(' ', '-')}-jobs-in-"
            f"{location.lower().replace(' ', '-')}"
        )
        await self.page.goto(url)
        await self.page.wait_for_load_state("networkidle")
        await self.human_delay()

        for card in await self.page.query_selector_all(".jobTuple"):
            try:
                title_el = await card.query_selector(".title")
                comp_el  = await card.query_selector(".companyInfo .subTitle")
                loc_el   = await card.query_selector(".location")
                link_el  = await card.query_selector("a.title")

                title   = await title_el.inner_text()        if title_el else ""
                company = await comp_el.inner_text()         if comp_el  else ""
                loc     = await loc_el.inner_text()          if loc_el   else ""
                href    = await link_el.get_attribute("href") if link_el  else ""

                if not title or not href:
                    continue

                jobs.append(Job(
                    id=f"nk_{abs(hash(href)) % 10**10}",
                    title=title.strip(), company=company.strip(),
                    location=loc.strip(), platform="Naukri",
                    url=href, description="",
                    posted_date=datetime.now().strftime("%Y-%m-%d"),
                ))
            except Exception:
                continue
        return jobs[:15]

    async def get_description(self, job: Job) -> str:
        try:
            await self.page.goto(job.url)
            await self.page.wait_for_load_state("networkidle")
            el = await self.page.query_selector(".job-desc")
            if el:
                return (await el.inner_text())[:3000]
        except Exception:
            pass
        return ""

    async def apply(self, job: Job, cover_note: str) -> bool:
        try:
            await self.page.goto(job.url)
            await self.page.wait_for_load_state("networkidle")
            await self.human_delay()

            btn = await self.page.query_selector('button[id="apply-button"]')
            if not btn:
                return False
            await btn.click()
            await self.human_delay()

            cover = await self.page.query_selector('textarea[name="coverLetter"]')
            if cover:
                await cover.fill(cover_note)

            submit = await self.page.query_selector('button[type="submit"]')
            if submit:
                await submit.click()
                await self.human_delay()
                console.log(f"[green]Naukri: Applied to {job.title} @ {job.company}[/green]")
                return True
        except Exception as e:
            console.log(f"[red]Naukri apply error: {e}[/red]")
        return False


class WellfoundScraper(BaseScraper):
    name = "wellfound"
    login_url = "https://wellfound.com/login"
    check_url = "https://wellfound.com/jobs"

    async def is_logged_in(self) -> bool:
        try:
            await self.safe_goto(self.check_url)
            current = self.page.url.lower()
            if "login" in current or "signin" in current or "sign_in" in current:
                return False
            logged_in_selectors = [
                '[data-test="UserMenu"]',
                '[data-testid="user-menu"]',
                'button[aria-label*="user" i]',
                'button[aria-label*="profile" i]',
                'a[href*="/profile/edit"]',
                'a[href*="/candidate/profile"]',
                'a:has-text("Your Profile")',
                'button:has-text("Log out")',
            ]
            for sel in logged_in_selectors:
                if await self.page.query_selector(sel):
                    return True
            return False
        except Exception as e:
            console.log(f"[yellow]Wellfound login check warning: {e}[/yellow]")
        return False

    async def login(self) -> bool:
        return await self.manual_login()

    async def search_jobs(self, query: str, location: str) -> List[Job]:
        jobs = []
        url  = f"https://wellfound.com/jobs?role={query.replace(' ', '%20')}&location={location}"
        await self.page.goto(url)
        await self.page.wait_for_load_state("networkidle")
        await self.human_delay()

        for card in await self.page.query_selector_all('[data-test="JobListingCard"]'):
            try:
                title_el = await card.query_selector('h2')
                comp_el  = await card.query_selector('[data-test="startup-name"]')
                link_el  = await card.query_selector('a')

                title   = await title_el.inner_text()        if title_el else ""
                company = await comp_el.inner_text()         if comp_el  else ""
                href    = await link_el.get_attribute("href") if link_el  else ""

                if not title or not href:
                    continue

                full_url = f"https://wellfound.com{href}" if href.startswith("/") else href
                jobs.append(Job(
                    id=f"wf_{abs(hash(full_url)) % 10**10}",
                    title=title.strip(), company=company.strip(),
                    location=location, platform="Wellfound",
                    url=full_url, description="",
                    posted_date=datetime.now().strftime("%Y-%m-%d"),
                ))
            except Exception:
                continue
        return jobs[:15]

    async def get_description(self, job: Job) -> str:
        try:
            await self.page.goto(job.url)
            await self.page.wait_for_load_state("networkidle")
            el = await self.page.query_selector('[data-test="JobDescription"]')
            if el:
                return (await el.inner_text())[:3000]
        except Exception:
            pass
        return ""

    async def apply(self, job: Job, cover_note: str) -> bool:
        try:
            await self.page.goto(job.url)
            await self.page.wait_for_load_state("networkidle")
            await self.human_delay()

            btn = await self.page.query_selector('[data-test="ApplyButton"]')
            if not btn:
                return False
            await btn.click()
            await self.human_delay()

            intro = await self.page.query_selector('textarea[name="introduction"]')
            if intro:
                await intro.fill(cover_note)

            submit = await self.page.query_selector('button[type="submit"]')
            if submit:
                await submit.click()
                await self.human_delay()
                console.log(f"[green]Wellfound: Applied to {job.title} @ {job.company}[/green]")
                return True
        except Exception as e:
            console.log(f"[red]Wellfound apply error: {e}[/red]")
        return False


# ─────────────────────────────────────────────
#  SETUP MODE — log in manually once
# ─────────────────────────────────────────────

async def run_setup():
    console.rule("[bold yellow]Setup — Manual Login[/bold yellow]")
    console.print(
        "\n[bold]Manual login setup.[/bold]\n"
        "A normal Chrome window will open for each platform. Log in manually,\n"
        "complete OTP/CAPTCHA/2FA if shown, then press Enter in this terminal.\n"
    )

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            executable_path=CHROME_EXECUTABLE,
            headless=False,
            slow_mo=80,
            no_viewport=True,
            args=BROWSER_ARGS,
        )
        page = context.pages[0] if context.pages else await context.new_page()

        scrapers = [
            LinkedInScraper(page),
            NaukriScraper(page),
            WellfoundScraper(page),
        ]

        for scraper in scrapers:
            name = scraper.name.title()
            console.rule(f"[cyan]{name}[/cyan]")
            success = await scraper.login()
            if not success:
                console.log(f"[red]{name}: login not verified; skipped[/red]")
            await asyncio.sleep(1)

        await context.close()

    console.print("\n[bold green]Setup complete! Session saved.[/bold green]")
    console.print("Run the agent anytime with:")
    console.print("  python job_agent_gpt.py\n")


# ─────────────────────────────────────────────
#  MAIN AGENT
# ─────────────────────────────────────────────

class JobAgent:
    SCRAPERS = {
        "linkedin":  LinkedInScraper,
        "naukri":    NaukriScraper,
        "wellfound": WellfoundScraper,
    }

    def __init__(self):
        self.db     = JobDB()
        self.scorer = JobScorer()
        self.stats  = {"scanned": 0, "matched": 0, "applied": 0, "skipped": 0, "failed": 0}

    async def run(self):
        console.rule("[bold]Job Application Agent Starting[/bold]")
        console.print(
            f"[dim]Threshold: {CONFIG['match_threshold']}%  |  "
            f"Max: {CONFIG['max_applications_per_run']} applications[/dim]\n"
        )

        async with async_playwright() as pw:
            context: BrowserContext = await pw.chromium.launch_persistent_context(
                user_data_dir=USER_DATA_DIR,
                executable_path=CHROME_EXECUTABLE,
                headless=False,
                slow_mo=80,
                no_viewport=True,
                args=BROWSER_ARGS,
            )

            for platform_name, enabled in CONFIG["platforms"].items():
                if not enabled:
                    continue
                if self.stats["applied"] >= CONFIG["max_applications_per_run"]:
                    console.log("[yellow]Max applications reached. Stopping.[/yellow]")
                    break

                console.rule(f"[cyan]{platform_name.title()}[/cyan]")
                page    = await context.new_page()
                scraper = self.SCRAPERS[platform_name](page)

                try:
                    if not await scraper.is_logged_in():
                        console.log(f"[yellow]{platform_name.title()}: no active session, logging in...[/yellow]")
                        if not await scraper.login():
                            console.log(f"[red]{platform_name.title()}: login failed, skipping.[/red]")
                            await page.close()
                            continue
                    else:
                        console.log(f"[green]{platform_name.title()}: session already active[/green]")

                    all_jobs: List[Job] = []
                    for query in CONFIG["search_queries"]:
                        console.log(f"Searching: [bold]{query}[/bold] in {CONFIG['location']}")
                        found = await scraper.search_jobs(query, CONFIG["location"])
                        all_jobs.extend(found)
                        self.stats["scanned"] += len(found)
                        await asyncio.sleep(1)

                    seen: set        = set()
                    unique: List[Job] = []
                    for j in all_jobs:
                        if j.id not in seen:
                            seen.add(j.id)
                            unique.append(j)

                    console.log(f"Found {len(unique)} unique jobs on {platform_name.title()}")

                    for job in unique:
                        if self.stats["applied"] >= CONFIG["max_applications_per_run"]:
                            break
                        if self.db.already_applied(job.id):
                            console.log(f"[dim]Already applied: {job.title}[/dim]")
                            continue

                        console.log(f"Reading: {job.title} @ {job.company}...")
                        job.description  = await scraper.get_description(job)
                        score, reason    = self.scorer.score(job)
                        job.match_score  = score
                        job.match_reason = reason

                        color = "green" if score >= 75 else "yellow" if score >= 60 else "red"
                        console.log(
                            f"  [{color}]{score}%[/{color}] — {job.title} @ {job.company}"
                        )

                        if score >= CONFIG["match_threshold"]:
                            self.stats["matched"] += 1
                            cover_note = self.scorer.generate_cover_note(job)
                            console.log(f"[cyan]Applying: {job.title} @ {job.company}[/cyan]")
                            success = await scraper.apply(job, cover_note)
                            if success:
                                job.applied    = True
                                job.applied_at = datetime.now().isoformat()
                                job.status     = "applied"
                                self.stats["applied"] += 1
                            else:
                                job.status = "failed"
                                self.stats["failed"] += 1
                        else:
                            job.status = "skipped"
                            self.stats["skipped"] += 1

                        self.db.upsert(job)
                        await asyncio.sleep(CONFIG["action_delay_min"])

                except Exception as e:
                    console.log(f"[red]{platform_name} error: {e}[/red]")
                finally:
                    await page.close()

            await context.close()

        self._print_report()
        self.db.export_csv()

    def _print_report(self):
        console.rule("[bold]Run Complete[/bold]")
        t = Table(show_header=True, header_style="bold")
        t.add_column("Metric")
        t.add_column("Count", justify="right")
        t.add_row("Jobs scanned",             str(self.stats["scanned"]))
        t.add_row("Matched (>=threshold)",    str(self.stats["matched"]))
        t.add_row("[green]Applied[/green]",   f"[green]{self.stats['applied']}[/green]")
        t.add_row("[yellow]Skipped[/yellow]", str(self.stats["skipped"]))
        t.add_row("[red]Failed[/red]",        str(self.stats["failed"]))
        console.print(t)

        applied = [j for j in self.db.get_all() if j.applied]
        if applied:
            console.print("\n[bold]Applications sent:[/bold]")
            for j in sorted(applied, key=lambda x: x.match_score, reverse=True):
                console.print(
                    f"  ✓ [green]{j.title}[/green] @ {j.company} "
                    f"({j.platform}) — {j.match_score}%"
                )


# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--setup", action="store_true",
        help="Open browser to log in manually (run once before first use)"
    )
    args = parser.parse_args()

    if args.setup:
        asyncio.run(run_setup())
    else:
        asyncio.run(JobAgent().run())
