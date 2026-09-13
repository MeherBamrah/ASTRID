"""
Browser agent: executes actions using real Google Chrome on Windows.
Features:
1. Auto-connects to existing running Google Chrome via Chrome DevTools Protocol (CDP)
   if Chrome is started with `--remote-debugging-port=9222` (uses your personal logged-in Chrome!).
2. Fallback: Launches real installed Google Chrome (`channel="chrome"`) as a native desktop application
   with persistent user data in `backend/chrome_profile` so logins stay saved.
3. Does not close the window abruptly so the user can take over manually anytime.
4. Intelligent selector resolution for search bars, quiz radio options, and Next buttons.
"""
import os
import asyncio
import base64
import json
import urllib.parse
import urllib.request
import websockets
from pathlib import Path
from playwright.async_api import async_playwright, Page, BrowserContext, Browser, Playwright
import httpx

CHROME_PROFILE_DIR = Path(__file__).resolve().parent.parent / "chrome_profile"


class BrowserSession:
    def __init__(self):
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.browser: Browser | None = None
        self.page: Page | None = None
        self.is_cdp: bool = False

    def is_closed(self) -> bool:
        if not self.page:
            return True
        try:
            return self.page.is_closed()
        except Exception:
            return True

    async def start(self, headless: bool = False):
        print(f"[BROWSER] start() called with headless={headless}...", flush=True)
        if self.page and not self.is_closed():
            print("[BROWSER] Existing page already alive and open!", flush=True)
            return

        if not self.playwright:
            print("[BROWSER] Starting playwright engine...", flush=True)
            self.playwright = await async_playwright().start()

        # 1. Check if user's real personal Google Chrome is running with remote debugging (port 9222)
        import socket
        port_open = False
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                port_open = (s.connect_ex(("127.0.0.1", 9222)) == 0)
        except Exception:
            port_open = False

        if port_open:
            try:
                print("[BROWSER] Port 9222 is open! Connecting over CDP...", flush=True)
                self.browser = await self.playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
                self.is_cdp = True
                # Loop across all contexts to pick the user's real profile window (not profile picker / chrome://)
                chosen_context = None
                chosen_page = None

                for ctx in self.browser.contexts:
                    for pg in ctx.pages:
                        u = pg.url.lower()
                        if not u.startswith("chrome://") and not u.startswith("chrome-extension://") and "localhost:3000" not in u and not pg.is_closed():
                            chosen_context = ctx
                            chosen_page = pg
                            break
                    if chosen_page:
                        break

                if not chosen_context:
                    # Use the last context (most recently focused user profile)
                    chosen_context = self.browser.contexts[-1] if self.browser.contexts else await self.browser.new_context()

                self.context = chosen_context
                if not chosen_page:
                    # Filter out chrome:// internal tabs if possible
                    for pg in self.context.pages:
                        if not pg.url.startswith("chrome://") and not pg.is_closed():
                            chosen_page = pg
                            break
                    if not chosen_page:
                        self.page = await self.context.new_page()
                    else:
                        self.page = chosen_page
                else:
                    self.page = chosen_page

                try:
                    await self.context.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined
                        });
                        if (!window.chrome) {
                            window.chrome = { runtime: {} };
                        }
                    """)
                except Exception:
                    pass

                await self.page.bring_to_front()
                print(f"[BROWSER] Successfully attached to real Google Chrome browser! Page URL: {self.page.url}", flush=True)
                return
            except Exception as e:
                print(f"[BROWSER] Port 9222 connection failed: {e}. Falling back to visible desktop browser.", flush=True)

        # 2. Launch real Google Chrome desktop browser window (persistent profile in backend/astrid_chrome_profile)
        profile_dir = Path(__file__).resolve().parent.parent / "astrid_chrome_profile"
        profile_dir.mkdir(parents=True, exist_ok=True)

        for lock_file in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
            lp = profile_dir / lock_file
            if lp.exists():
                try:
                    lp.unlink(missing_ok=True)
                except Exception:
                    pass

        print(f"[BROWSER] Launching real Google Chrome desktop window (headless={headless})...", flush=True)
        try:
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir.resolve()),
                channel="chrome",  # Real Google Chrome installed on user's system
                headless=headless,
                args=[
                    "--start-maximized",
                    "--new-window",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-blink-features=AutomationControlled"
                ],
                ignore_default_args=["--enable-automation"]
            )
            print("[BROWSER] Real Google Chrome window launched successfully!", flush=True)
        except Exception as e:
            print(f"[BROWSER] Warning launching real Chrome channel: {e}. Falling back to default chromium window.", flush=True)
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir.resolve()),
                headless=headless,
                args=[
                    "--start-maximized",
                    "--new-window",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--disable-blink-features=AutomationControlled"
                ],
                ignore_default_args=["--enable-automation"]
            )
            print("[BROWSER] Chromium desktop window launched successfully!", flush=True)

        if len(self.context.pages) > 0:
            self.page = self.context.pages[0]
        else:
            self.page = await self.context.new_page()

        try:
            await self.page.bring_to_front()
        except Exception:
            pass

    async def manual_click(self, x: int, y: int):
        """Dispatches a mouse click to (x, y) coordinates on the live page."""
        if self.page:
            try:
                await self.page.mouse.click(x, y)
                await self.page.wait_for_timeout(500)
            except Exception as e:
                print(f"Manual click error: {e}")

    async def manual_type(self, text: str, press_enter: bool = False):
        """Types text directly into the currently focused element."""
        if self.page:
            try:
                await self.page.keyboard.type(text)
                if press_enter:
                    await self.page.keyboard.press("Enter")
                await self.page.wait_for_timeout(800)
            except Exception as e:
                print(f"Manual type error: {e}")

    async def manual_navigate(self, url: str):
        """Navigates to a specific URL."""
        if self.page:
            try:
                if not (url.startswith("http://") or url.startswith("https://")):
                    url = f"https://{url}"
                await self.page.goto(url, wait_until="domcontentloaded", timeout=25000)
                await self.page.wait_for_timeout(1000)
            except Exception as e:
                print(f"Manual navigate error: {e}")

    async def manual_scroll(self, dy: int = 400):
        """Scrolls the page vertically by dy pixels."""
        if self.page:
            try:
                await self.page.mouse.wheel(0, dy)
                await self.page.wait_for_timeout(400)
            except Exception as e:
                print(f"Manual scroll error: {e}")

    async def take_screenshot(self) -> str:
        """Captures page screenshot as base64 jpeg for live streaming."""
        if not self.page:
            return ""
        try:
            shot_bytes = await self.page.screenshot(type="jpeg", quality=75)
            return base64.b64encode(shot_bytes).decode("utf-8")
        except Exception as e:
            return ""

    def _get_target_frames(self):
        """Returns all active frames (main frame first, then all embedded quiz/tool iframes)."""
        if not self.page:
            return []
        frames = [self.page.main_frame]
        for f in self.page.frames:
            if f != self.page.main_frame and not f.is_detached():
                frames.append(f)
        return frames

    async def _eval_scorm_quiz(self, js_code: str):
        """Executes JavaScript inside the embedded SCORM/LTI quiz document (e.g. AWS Academy / Storyline) via CDP."""
        try:
            req = urllib.request.urlopen('http://127.0.0.1:9222/json/list', timeout=1.5)
            targets = json.loads(req.read().decode())
            cc_targets = [t for t in targets if "contentcontroller" in t.get("url", "")]
            if not cc_targets:
                return None

            # Iterate through targets (newest/active first)
            for target in reversed(cc_targets):
                ws_url = target.get("webSocketDebuggerUrl")
                if not ws_url:
                    continue
                try:
                    async with websockets.connect(ws_url, close_timeout=2) as ws:
                        wrapper = f"""(() => {{
                            function getQuizDoc() {{
                                try {{
                                    const d1 = document.getElementById('dispatch_content_frame');
                                    const doc1 = d1.contentDocument || d1.contentWindow.document;
                                    const d2 = doc1.getElementById('contentRelay');
                                    const doc2 = d2.contentDocument || d2.contentWindow.document;
                                    const d3 = doc2.getElementById('ScormContent');
                                    return d3.contentDocument || d3.contentWindow.document;
                                }} catch(e) {{
                                    return null;
                                }}
                            }}
                            const qDoc = getQuizDoc();
                            if (!qDoc) return null;
                            {js_code}
                        }})()"""
                        await ws.send(json.dumps({
                            "id": 1,
                            "method": "Runtime.evaluate",
                            "params": {"expression": wrapper, "returnByValue": True}
                        }))
                        res = await ws.recv()
                        data = json.loads(res)
                        val = data.get("result", {}).get("result", {}).get("value")
                        if val is not None:
                            return val
                except Exception:
                    continue
            return None
        except Exception:
            return None

    async def get_page_summary(self) -> str:
        """Extracts current URL, title, visible quiz/interactive inputs, and text across all page frames and iframes."""
        if not self.page:
            return "No page active"
        try:
            url = self.page.url
            title = await self.page.title()
            
            # 1. Main frame text
            text = await self.page.inner_text("body")
            trimmed_text = " ".join(text.split())[:1000]

            inputs_summary = ""
            try:
                inputs = await self.page.eval_on_selector_all(
                    "input, button, select, [role='radio'], [role='button']",
                    """elements => elements.slice(0, 15).map(el => {
                        return `${el.tagName.toLowerCase()}[type='${el.type || ''}' text='${(el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '').trim()}']`;
                    }).join(' | ')"""
                )
                if inputs:
                    inputs_summary = f"\nKey Elements: {inputs}"
            except Exception:
                pass

            iframe_details = ""
            # 2. Check for embedded SCORM quiz (e.g. AWS Academy Knowledge Checks, Storyline player)
            try:
                scorm_info = await self._eval_scorm_quiz("""
                    const body = (qDoc.body.innerText || '').trim();
                    const optionInputs = Array.from(qDoc.querySelectorAll('input.acc-radio, input.acc-checkbox, input[type="radio"], input[type="checkbox"]'));
                    const isCheckbox = optionInputs.some(inp => inp.type === 'checkbox' || inp.classList.contains('acc-checkbox'));
                    const optionLabels = optionInputs.map(inp => {
                        const lbl = qDoc.querySelector('label[for="' + inp.id + '"]');
                        return lbl ? (lbl.innerText || '').trim() : '';
                    }).filter(Boolean);
                    const buttons = Array.from(qDoc.querySelectorAll('button, [role="button"], #submit')).map(b => (b.innerText || b.getAttribute('aria-label') || '').trim()).filter(Boolean);
                    return {
                        body: body.substring(0, 1500),
                        buttons: Array.from(new Set(buttons)),
                        options: Array.from(new Set(optionLabels)),
                        isCheckbox: isCheckbox
                    };
                """)
                if scorm_info and isinstance(scorm_info, dict) and scorm_info.get("body"):
                    b_text = scorm_info.get("body", "").replace('\u00a0', ' ').replace('&nbsp;', ' ')
                    btns = scorm_info.get("buttons", [])
                    opts = scorm_info.get("options", [])
                    input_type = "Checkbox (Multi-Select)" if scorm_info.get("isCheckbox") else "Radio (Single-Select)"
                    iframe_details += f"\n\n--- [ACTIVE AWS ACADEMY / SCORM KNOWLEDGE CHECK QUIZ] ---\n{b_text}\nSelection Type: {input_type}\nAvailable Buttons: {btns}\nAvailable Options: {opts}"
            except Exception:
                pass

            if iframe_details:
                full_summary = f"Page Title: {title}\n{iframe_details}"
                return full_summary.replace('\u00a0', ' ').replace('&nbsp;', ' ')

            # 3. Inspect standard iframes if SCORM wasn't found
            for frame in self.page.frames:
                if frame == self.page.main_frame or frame.is_detached():
                    continue
                try:
                    frame_text = await frame.inner_text("body", timeout=1200)
                    frame_clean = " ".join(frame_text.split())
                    if len(frame_clean) > 15:
                        iframe_details += f"\n\n--- [ACTIVE QUIZ / TOOL IFRAME] ---\n{frame_clean[:1800]}"
                except Exception:
                    continue

            full_summary = f"Current URL: {url}\nPage Title: {title}{inputs_summary}\nVisible Content: {trimmed_text}{iframe_details}"
            return full_summary.encode("ascii", "replace").decode("ascii")
        except Exception:
            return ""

    async def execute(self, action: dict) -> str:
        """Runs one action dict with intelligent element, SCORM quiz, and option resolution."""
        if not self.page:
            return "Browser not initialized"

        act = action.get("action", "").lower().strip()
        target = action.get("target", "").strip()
        value = action.get("value", "").strip()
        target_lower = target.lower().strip()
        value_lower = value.lower().strip()
        frames = self._get_target_frames()

        try:
            # SPECIAL: NATIVE CLOUD LAB EXECUTION (AWS Vocareum Labs)
            if act == "execute_aws_lab":
                lab_target = (target or value or "").lower()
                if "lab 6" in lab_target or "scale" in lab_target or "5426876" in lab_target:
                    from agents.aws_lab_executor import execute_lab_6
                    res = await execute_lab_6(progress_callback=print)
                    return res
                return f"Executed AWS Lab action: {target}"

            # SPECIAL: SCORM LTI Quiz Actions (AWS Academy, Articulate Storyline)
            # 1. Start button
            if (act == "click" and any(k in target_lower for k in ["start", "begin", "start quiz", "take quiz"])) or "start" in value_lower:
                scorm_res = await self._eval_scorm_quiz("""
                    const startBtn = qDoc.querySelector('button.acc-button') || 
                                     Array.from(qDoc.querySelectorAll('button, [role="button"], a, div')).find(e => (e.innerText || '').trim().toLowerCase() === 'start');
                    if (startBtn) {
                        startBtn.click();
                        return 'Clicked Start button in quiz!';
                    }
                    return null;
                """)
                if scorm_res:
                    await asyncio.sleep(2)
                    return "Started AWS Knowledge Check quiz successfully"

            # 2. Select quiz option / answer (supports single or multi-select choose two/three)
            if act in ["select_option", "choose_option", "radio", "checkbox"] or (act == "click" and any(k in target_lower for k in ["option", "choice", "/", "amazon", "aws", "ec2", "vpc", "lambda"])):
                opt_label = value or target
                json_raw_target = json.dumps(opt_label.lower().strip())

                scorm_res = await self._eval_scorm_quiz(f"""
                    // 0. Dismiss any blocking dialog (e.g. Invalid Answer popup)
                    const okBtns = Array.from(qDoc.querySelectorAll('button, [role="button"], div')).filter(b => 
                        ['ok', 'continue'].includes((b.innerText || '').trim().toLowerCase()) || 
                        (b.id && (b.id.includes('InvalidPromptSlide') || b.id.includes('btn0')))
                    );
                    for (const b of okBtns) b.click();

                    const rawTarget = {json_raw_target};
                    const inputs = Array.from(qDoc.querySelectorAll('input.acc-checkbox, input.acc-radio, input[type="checkbox"], input[type="radio"]'));

                    let selectedCount = 0;
                    if (inputs.length > 0) {{
                        for (const inp of inputs) {{
                            const lbl = qDoc.querySelector('label[for="' + inp.id + '"]');
                            const lblText = (lbl ? lbl.innerText : '').trim();
                            const lblLower = lblText.toLowerCase();

                            // Match either full equality or multi-choice substring containment
                            const isExactMatch = (rawTarget === lblLower);
                            const isMultiMatch = (lblLower.length > 4 && rawTarget.includes(lblLower));
                            const isMatch = isExactMatch || isMultiMatch;

                            const isCheckbox = inp.type === 'checkbox' || inp.classList.contains('acc-checkbox');

                            if (isMatch) {{
                                if (!inp.checked) {{
                                    if (lbl) lbl.click();
                                    if (!inp.checked) inp.click();
                                    if (!inp.checked) {{
                                        const modelEl = qDoc.querySelector('[data-acc-text="' + lblText + '"], [data-model-id="' + inp.id.replace('acc-', '') + '"]');
                                        if (modelEl) modelEl.click();
                                    }}
                                }}
                                selectedCount++;
                            }} else if (isCheckbox && inp.checked) {{
                                // Deselect option if it was accidentally checked
                                if (lbl) lbl.click();
                                if (inp.checked) inp.click();
                            }}
                        }}
                    }}

                    // Fallback: If no direct inputs matched, search by labels
                    if (selectedCount === 0) {{
                        const labels = Array.from(qDoc.querySelectorAll('label'));
                        for (const matchLabel of labels) {{
                            const lblText = (matchLabel.innerText || '').trim().toLowerCase();
                            if (!lblText) continue;
                            if (rawTarget === lblText || (lblText.length > 4 && rawTarget.includes(lblText))) {{
                                matchLabel.click();
                                selectedCount++;
                            }}
                        }}
                    }}

                    return selectedCount > 0;
                """)
                if scorm_res:
                    await asyncio.sleep(0.6)
                    # Automatically click SUBMIT
                    await self._eval_scorm_quiz("""
                        const sub = qDoc.getElementById('submit') || 
                                    Array.from(qDoc.querySelectorAll('button, [role="button"], a, div')).find(e => (e.innerText || '').trim().toUpperCase() === 'SUBMIT');
                        if (sub) sub.click();
                    """)
                    await asyncio.sleep(0.8)

                    # Extract feedback and record to persistent quiz memory
                    try:
                        feedback_info = await self._eval_scorm_quiz("""
                            const bodyText = (qDoc.body.innerText || '').trim();
                            const layers = Array.from(qDoc.querySelectorAll('.slide-layer.shown, .slide-layer, .feedback'));
                            const layerText = layers.map(l => (l.innerText || '').trim()).join('\\n');
                            const optionInputs = Array.from(qDoc.querySelectorAll('input.acc-radio, input.acc-checkbox, input[type="radio"], input[type="checkbox"]'));
                            const optionLabels = optionInputs.map(inp => {
                                const lbl = qDoc.querySelector('label[for="' + inp.id + '"]');
                                return lbl ? (lbl.innerText || '').trim() : '';
                            }).filter(Boolean);
                            return {
                                question: bodyText.substring(0, 500),
                                feedback: layerText.length > 5 ? layerText : bodyText,
                                options: Array.from(new Set(optionLabels))
                            };
                        """)
                        if feedback_info and isinstance(feedback_info, dict):
                            from db.quiz_memory import record_quiz_outcome
                            all_opts = feedback_info.get("options", [])
                            # If opt_label matches an entire option, it is a single multi-clause option
                            if all_opts and any(o.lower().strip() == opt_label.lower().strip() for o in all_opts):
                                final_selected = [opt_label]
                            elif "," in opt_label:
                                final_selected = [p.strip() for p in opt_label.split(",") if p.strip()]
                            else:
                                final_selected = [opt_label]

                            record_quiz_outcome(
                                question=feedback_info.get("question", ""),
                                options=all_opts or final_selected,
                                selected=final_selected,
                                feedback_text=feedback_info.get("feedback", "")
                            )
                    except Exception as e:
                        print(f"[QUIZ MEMORY] Feedback capture warning: {e}", flush=True)

                    # Automatically click Continue / OK on feedback dialog
                    await self._eval_scorm_quiz("""
                        const okBtn = Array.from(qDoc.querySelectorAll('button, [role="button"], a, div')).find(e => ['continue', 'ok'].includes((e.innerText || '').trim().toLowerCase()));
                        if (okBtn) okBtn.click();
                    """)
                    await asyncio.sleep(0.8)
                    disp_opts = [p.strip() for p in opt_label.split(",") if p.strip()] if "," in opt_label else [opt_label]
                    return f"Selected option(s) {', '.join(disp_opts)}, submitted, and advanced"

            # 3. Next question / Submit / Continue on SCORM quiz
            if act in ["click", "done"] and any(k in target_lower for k in ["next", "continue", "submit", "finish", "ok", "completed"]):
                scorm_res = await self._eval_scorm_quiz("""
                    const btns = Array.from(qDoc.querySelectorAll('button, [role="button"], a, div'));
                    const nxt = qDoc.getElementById('submit') || 
                                qDoc.getElementById('next') || 
                                btns.find(e => ['continue', 'next', 'ok', 'submit', 'finish'].includes((e.innerText || '').trim().toLowerCase()));
                    if (nxt) {
                        nxt.click();
                        return 'Clicked quiz Next/Continue';
                    }
                    return null;
                """)
                if scorm_res:
                    await asyncio.sleep(1.5)
                    return "Advanced in quiz"

            # STANDARD PLAYWRIGHT ACTIONS (Fallback for non-SCORM sites)
            # 1. NAVIGATION
            if act == "navigate":
                if not (target.startswith("http://") or target.startswith("https://")):
                    if " " in target or "." not in target:
                        target = f"https://duckduckgo.com/?q={urllib.parse.quote(target)}"
                    else:
                        target = f"https://{target}"
                await self.page.goto(target, wait_until="domcontentloaded", timeout=25000)
                await self.page.wait_for_timeout(1500)

            # 2. SELECT QUIZ OPTION / RADIO BUTTON
            elif act in ["select_option", "choose_option", "radio"]:
                selected = False
                opt_label = value or target
                opt_clean = opt_label.removeprefix("Option ").removeprefix("option ").strip()
                target_frames = list(reversed(frames))  # Check quiz iframes first

                for frame in target_frames:
                    if selected:
                        break
                    for sel in [
                        f"label:has-text('{opt_clean}')",
                        f"input[type='radio'][value*='{opt_clean}' i]",
                        f"[role='radio']:has-text('{opt_clean}')",
                        f"div[role='radio']:has-text('{opt_clean}')",
                        f"button:has-text('{opt_clean}')",
                        f"div:has-text('{opt_clean}')"
                    ]:
                        try:
                            loc = frame.locator(sel).first
                            if await loc.is_visible(timeout=800):
                                await loc.click()
                                selected = True
                                break
                        except Exception:
                            continue

                if not selected:
                    for frame in target_frames:
                        try:
                            loc = frame.get_by_text(opt_clean, exact=False).first
                            if await loc.is_visible(timeout=1000):
                                await loc.click()
                                selected = True
                                break
                        except Exception:
                            continue

                await self.page.wait_for_timeout(1000)

            # 3. TYPING INTO INPUT OR SEARCH BAR
            elif act == "type":
                typed = False
                search_selectors = [
                    "input[name='search_query']",     # YouTube
                    "input[name='q']",                # Google, DuckDuckGo
                    "input[type='search']",
                    "input[placeholder*='Search' i]",
                    "input[aria-label*='Search' i]",
                    "textarea[name='q']",
                    "input#search",
                    "input[type='text']",
                ]

                target_lower = target.lower()
                if "search" in target_lower or target_lower in ["search bar", "search input", "query", ""]:
                    for frame in frames:
                        if typed:
                            break
                        for sel in search_selectors:
                            try:
                                loc = frame.locator(sel).first
                                if await loc.is_visible(timeout=1000):
                                    await loc.click()
                                    await loc.fill(value)
                                    await self.page.keyboard.press("Enter")
                                    typed = True
                                    break
                            except Exception:
                                continue

                if not typed:
                    target_cleaned = target.removeprefix("text=").strip()
                    for frame in frames:
                        if typed:
                            break
                        try:
                            loc = frame.locator(target).first
                            if await loc.is_visible(timeout=1000):
                                await loc.fill(value)
                                typed = True
                                break
                        except Exception:
                            pass
                        try:
                            el = frame.get_by_placeholder(target_cleaned, exact=False).first
                            if await el.is_visible(timeout=1000):
                                await el.fill(value)
                                typed = True
                                break
                        except Exception:
                            pass

                if not typed:
                    await self.page.keyboard.type(value)

                await self.page.wait_for_timeout(2000)

            # 4. CLICKING BUTTONS, LINKS, OR NEXT QUESTION
            elif act == "click":
                clicked = False
                target_lower = target.lower().strip()
                target_cleaned = target.removeprefix("text=").strip()
                # Prioritize child iframes (quiz frames) over outer LMS canvas page
                target_frames = list(reversed(frames))

                # Special handling for "Start" button on quizzes / AWS Academy
                if target_lower in ["start", "start quiz", "begin", "continue"]:
                    for frame in target_frames:
                        if clicked:
                            break
                        for start_sel in ["button:has-text('Start')", "[role='button']:has-text('Start')", "a:has-text('Start')", "button:has-text('Begin')"]:
                            try:
                                loc = frame.locator(start_sel).first
                                if await loc.is_visible(timeout=1000):
                                    await loc.click()
                                    clicked = True
                                    break
                            except Exception:
                                continue

                # Quiz "Next Question", "Check Answer", or "Submit" handling
                if not clicked and any(k in target_lower for k in ["next", "next question", "submit", "finish", "check"]):
                    for frame in target_frames:
                        if clicked:
                            break
                        # If child frame (quiz iframe), prioritize inside frame!
                        # Do NOT click outer Canvas LMS course navigation like .next-module-link
                        next_selectors = [
                            "button:has-text('Next')",
                            "input[value*='Next' i]",
                            "button[id*='next' i]",
                            "button:has-text('Submit')",
                            "input[value*='Submit' i]",
                            "button:has-text('Finish')",
                            "button:has-text('Check')"
                        ]
                        if frame != self.page.main_frame:
                            next_selectors.insert(0, "a:has-text('Next')")

                        for nxt in next_selectors:
                            try:
                                loc = frame.locator(nxt).first
                                if await loc.is_visible(timeout=1000):
                                    await loc.click()
                                    clicked = True
                                    break
                            except Exception:
                                continue

                # General clicking across all frames
                if not clicked:
                    for frame in target_frames:
                        if clicked:
                            break
                        for locator_fn in [
                            lambda f: f.get_by_role("button", name=target_cleaned).first,
                            lambda f: f.locator(f"button:has-text('{target_cleaned}')").first,
                            lambda f: f.get_by_text(target_cleaned, exact=False).first,
                            lambda f: f.locator(target).first
                        ]:
                            try:
                                loc = locator_fn(frame)
                                if await loc.is_visible(timeout=1000):
                                    # Guard: Don't click outer Canvas LMS next-module link if target is a quiz action
                                    is_next_module = False
                                    try:
                                        cls = await loc.get_attribute("class") or ""
                                        if "next-module-link" in cls or "module-sequence-footer" in cls:
                                            is_next_module = True
                                    except Exception:
                                        pass
                                    if not is_next_module:
                                        await loc.click()
                                        clicked = True
                                        break
                            except Exception:
                                continue

                await self.page.wait_for_timeout(2000)

            # 5. KEY PRESS (Enter, Escape, etc.)
            elif act in ["press", "key", "enter"]:
                key_name = value or "Enter"
                await self.page.keyboard.press(key_name)
                await self.page.wait_for_timeout(1500)

            # 6. SCROLL
            elif act == "scroll":
                await self.page.mouse.wheel(0, 600)
                await self.page.wait_for_timeout(1000)

            # 7. READ / DONE
            elif act in ["read", "extract"]:
                await self.page.wait_for_timeout(1000)
            elif act == "done":
                return "DONE"

            # Return readable snapshot from main frame + any active quiz iframe
            combined_evidence = []
            try:
                main_text = await self.page.inner_text("body")
                combined_evidence.append(" ".join(main_text.split()))
            except Exception:
                pass

            for f in self.page.frames:
                if f != self.page.main_frame and not f.is_detached():
                    try:
                        f_text = await f.inner_text("body", timeout=1000)
                        if f_text and len(f_text.strip()) > 10:
                            combined_evidence.append("QUIZ FRAME: " + " ".join(f_text.split()))
                    except Exception:
                        pass

            full_evidence = " | ".join(combined_evidence)
            return full_evidence[:4000]

        except Exception as err:
            return f"Action {act} encountered error: {str(err)}"

        except Exception as err:
            return f"Action {act} encountered error: {str(err)}"

    async def close(self):
        # In CDP mode we don't kill the user's Chrome!
        if self.is_cdp:
            if self.browser:
                await self.browser.close()
        else:
            if self.context:
                await self.context.close()
        if self.playwright:
            await self.playwright.stop()
