"""
Accessibility-tree based element targeting for cortex41.

Approach used by browser-use, BrowserClaw, and Playwright MCP (2024-2025 state-of-the-art):
  1. Enumerate interactive elements via JavaScript (role + name + bounding rect)
  2. Annotate screenshot with numbered boxes (visual SoM) for the vision model
  3. Model outputs element_id → execute via Playwright semantic locators
     (get_by_role, get_by_label, get_by_text) — no coordinates, no CSS fragility

NOTE: page.accessibility was removed in Playwright >= 1.46.
      We use pure JS enumeration instead — more reliable across Playwright versions.
"""

import base64
import io
import re
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import Page


# ── JS element collection ──────────────────────────────────────────────────────

_COLLECT_ELEMENTS_JS = """
() => {
    const interactiveRoles = [
        'button','link','checkbox','radio','combobox','listbox',
        'menuitem','menuitemcheckbox','menuitemradio','option',
        'searchbox','slider','spinbutton','switch','tab','textbox',
        'treeitem','gridcell',
    ];

    function getRole(el) {
        const ariaRole = (el.getAttribute('role') || '').toLowerCase();
        if (ariaRole && interactiveRoles.includes(ariaRole)) return ariaRole;
        const tag = el.tagName.toLowerCase();
        if (tag === 'a' && el.href) return 'link';
        if (tag === 'button') return 'button';
        if (tag === 'select') return 'combobox';
        if (tag === 'textarea') return 'textbox';
        if (tag === 'input') {
            const t = (el.type || 'text').toLowerCase();
            if (t === 'checkbox') return 'checkbox';
            if (t === 'radio') return 'radio';
            if (t === 'submit' || t === 'button' || t === 'reset' || t === 'image') return 'button';
            if (t === 'search') return 'searchbox';
            if (t === 'range') return 'slider';
            if (t === 'number') return 'spinbutton';
            if (t === 'hidden' || t === 'file') return null;
            return 'textbox';
        }
        if (ariaRole) return ariaRole;
        return null;
    }

    function getName(el) {
        const labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {
            const ref = document.getElementById(labelledBy);
            if (ref && ref.textContent.trim()) return ref.textContent.trim().slice(0, 80);
        }
        return (
            el.getAttribute('aria-label') ||
            el.getAttribute('placeholder') ||
            el.getAttribute('title') ||
            el.getAttribute('alt') ||
            el.value ||
            (el.innerText || el.textContent || '')
        ).trim().slice(0, 80);
    }

    function isVisible(el) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) return false;
        if (rect.bottom < 0 || rect.top > window.innerHeight) return false;
        if (rect.right < 0 || rect.left > window.innerWidth) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        if (parseFloat(style.opacity) < 0.05) return false;
        return true;
    }

    const seen = new Set();
    const results = [];

    // ── Shadow DOM piercing collector ────────────────────────────────────────────
    // Recurse into shadow roots to find elements inside Web Components
    // (YouTube, Google Search, React portals, Material UI all use shadow DOM)
    function collectFromRoot(root, depth) {
        if (depth > 6) return;
        const selectors = [
            'a[href]', 'button', 'input:not([type="hidden"])', 'select', 'textarea',
            '[role]', '[tabindex]:not([tabindex="-1"])',
        ];
        selectors.forEach(sel => {
            try {
                root.querySelectorAll(sel).forEach(el => {
                    if (seen.has(el)) return;
                    seen.add(el);
                    if (el.disabled || el.getAttribute('aria-disabled') === 'true') return;
                    if (!isVisible(el)) return;
                    const role = getRole(el);
                    if (!role) return;
                    const name = getName(el);
                    const rect = el.getBoundingClientRect();
                    results.push({
                        role,
                        name: name || '',
                        value: (el.value != null ? String(el.value) : '').slice(0, 40),
                        description: (el.getAttribute('aria-description') || '').slice(0, 60),
                        x: Math.round(rect.left),
                        y: Math.round(rect.top),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height),
                        inShadow: depth > 0,
                    });
                });
            } catch(e) {}
        });
        // Recurse into shadow roots found at this level
        try {
            root.querySelectorAll('*').forEach(el => {
                if (el.shadowRoot) collectFromRoot(el.shadowRoot, depth + 1);
            });
        } catch(e) {}
    }

    collectFromRoot(document, 0);

    // Sort top-to-bottom, left-to-right for natural reading order
    results.sort((a, b) => a.y !== b.y ? a.y - b.y : a.x - b.x);
    return results.slice(0, 60);
}
"""


async def get_accessibility_elements(page: Page) -> list[dict]:
    """
    Extract all interactive elements visible in the current viewport.
    Returns list of {role, name, value, description, x, y, w, h} dicts.
    """
    try:
        elements = await page.evaluate(_COLLECT_ELEMENTS_JS)
        return elements or []
    except Exception as e:
        print(f"[SoM] element collection failed: {e}")
        return []


# ── Visual annotation ──────────────────────────────────────────────────────────

_PALETTE = [
    (220, 50,  50),
    (50,  100, 220),
    (50,  160, 50),
    (220, 130, 30),
    (160, 50,  160),
    (30,  160, 160),
    (220, 50,  150),
    (100, 50,  220),
]


def _draw_labels(img: Image.Image, elements: list[dict]) -> Image.Image:
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 11)
    except Exception:
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 11)
        except Exception:
            font = ImageFont.load_default()

    for i, el in enumerate(elements):
        x, y, w, h = el["x"], el["y"], el["w"], el["h"]
        if w <= 0 or h <= 0:
            continue
        cr, cg, cb = _PALETTE[i % len(_PALETTE)]

        draw.rectangle([x, y, x + w, y + h], fill=(cr, cg, cb, 40), outline=(cr, cg, cb, 200), width=2)

        label = str(i + 1)
        bw = max(18, len(label) * 8 + 6)
        bh = 18
        bx, by = max(0, x - 1), max(0, y - bh) if y >= bh else y
        draw.rectangle([bx, by, bx + bw, by + bh], fill=(cr, cg, cb, 220))
        draw.text((bx + 3, by + 2), label, fill=(255, 255, 255, 255), font=font)

    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


# ── Public API ─────────────────────────────────────────────────────────────────

async def build_som(page: Page, screenshot_b64: str) -> tuple[str, list[dict]]:
    """
    Build Set-of-Marks annotated screenshot.

    Returns (annotated_screenshot_b64, elements_list)
    elements_list[i] → label i+1 on the screenshot.
    Each element: {role, name, value, description, x, y, w, h}
    """
    elements = await get_accessibility_elements(page)
    if not elements:
        return screenshot_b64, []

    try:
        img = Image.open(io.BytesIO(base64.b64decode(screenshot_b64)))
        img = _draw_labels(img, elements)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        annotated = base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        print(f"[SoM] annotation failed: {e}")
        annotated = screenshot_b64

    return annotated, elements


async def click_som_element(page: Page, element_id: int, elements: list[dict], timeout_ms: int = 5000) -> bool:
    """
    Click element by its 1-indexed SoM label using Playwright semantic locators.
    Tries in order: get_by_role → get_by_role(regex) → get_by_label → get_by_text → get_by_placeholder
    """
    if element_id < 1 or element_id > len(elements):
        print(f"[SoM] element_id={element_id} out of range (have {len(elements)})")
        return False

    el = elements[element_id - 1]
    role = el.get("role", "")
    name = el.get("name", "")
    value = el.get("value", "")
    description = el.get("description", "")

    search_name = name or value or description
    print(f"[SoM] Clicking #{element_id}: role={role} name={search_name!r}")

    # Map our role names to Playwright ARIA roles
    _ROLE_MAP = {
        "textbox": "textbox", "searchbox": "searchbox",
        "button": "button", "link": "link",
        "checkbox": "checkbox", "radio": "radio",
        "combobox": "combobox", "listbox": "listbox",
        "menuitem": "menuitem", "menuitemcheckbox": "menuitemcheckbox",
        "option": "option", "tab": "tab",
        "switch": "switch", "slider": "slider",
        "spinbutton": "spinbutton", "gridcell": "gridcell",
    }

    pw_role = _ROLE_MAP.get(role)

    strategies = []

    # Strategy 1: get_by_role with exact name
    if pw_role and search_name:
        strategies.append(lambda r=pw_role, n=search_name: page.get_by_role(r, name=n).first)

    # Strategy 2: get_by_role with regex (partial match, case-insensitive)
    if pw_role and search_name:
        strategies.append(lambda r=pw_role, n=search_name: page.get_by_role(r, name=re.compile(re.escape(n[:30]), re.IGNORECASE)).first)

    # Strategy 3: get_by_label
    if search_name:
        strategies.append(lambda n=search_name: page.get_by_label(n).first)

    # Strategy 4: get_by_text (exact) — skip for input fields
    if search_name and role not in ("textbox", "searchbox", "combobox", "slider", "spinbutton"):
        strategies.append(lambda n=search_name: page.get_by_text(n, exact=True).first)

    # Strategy 5: get_by_placeholder — for input fields
    if search_name and role in ("textbox", "searchbox"):
        strategies.append(lambda n=search_name: page.get_by_placeholder(n).first)

    # Strategy 6: coordinate fallback using stored bounding rect
    x, y = el.get("x", 0), el.get("y", 0)
    w, h = el.get("w", 0), el.get("h", 0)
    if w > 0 and h > 0:
        cx, cy = x + w // 2, y + h // 2
        strategies.append(lambda cx=cx, cy=cy: _coord_click_locator(page, cx, cy))

    for i, strategy in enumerate(strategies):
        try:
            locator = strategy()
            if hasattr(locator, 'scroll_into_view_if_needed'):
                await locator.scroll_into_view_if_needed(timeout=2000)
                await locator.click(timeout=timeout_ms)
            else:
                # coordinate fallback returns a coroutine directly
                await locator
            print(f"[SoM] Clicked #{element_id} via strategy {i+1}")
            return True
        except Exception as e:
            print(f"[SoM] Strategy {i+1} failed for #{element_id}: {e}")
            continue

    print(f"[SoM] All strategies failed for #{element_id}")
    return False


async def _coord_click_locator(page: Page, x: int, y: int):
    """Coordinate click as last-resort fallback."""
    await page.mouse.click(x, y)


async def cleanup_som(page: Page):
    """No-op — JS-based approach doesn't inject persistent DOM attributes."""
    pass


def describe_elements(elements: list[dict]) -> str:
    """Compact text list for the vision model prompt."""
    if not elements:
        return "No interactive elements found."
    lines = []
    for i, el in enumerate(elements):
        role = el.get("role", "?")
        name = el.get("name", "")
        value = el.get("value", "")
        desc = el.get("description", "")
        display = name or value or desc
        line = f"[{i+1}] {role}"
        if display:
            line += f': "{display}"'
        lines.append(line)
    return "\n".join(lines)
