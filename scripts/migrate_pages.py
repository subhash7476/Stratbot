# scripts/migrate_pages.py
import re
import shutil
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "pages"
BACKUP = ROOT / "pages_backup" / datetime.now().strftime("%Y%m%d_%H%M%S")

# Replacement patterns
REPLACEMENTS = {
    # Remove local compute_supertrend entirely
    r"def\s+compute_supertrend\s*\(": "# LOCAL compute_supertrend removed — use core.indicators.compute_supertrend\n# def compute_supertrend(",

    # Replace old imports
    r"from\s+auth\.auth_manager\s+import\s+get_access_token": "from core.config import get_access_token",
    r"import\s+upstox_hybrid_wrapper": "from core.api.upstox_client import UpstoxClient",
    r"from\s+upstox_hybrid_wrapper\s+import\s+UpstoxHybrid": "from core.api.upstox_client import UpstoxClient",
}

# Required imports to insert if missing
REQUIRED_IMPORTS = [
    "from core.indicators import compute_supertrend",
    "from core.config import get_access_token",
]

def ensure_required_imports(text):
    lines = text.splitlines()
    existing = set(l.strip() for l in lines)

    insert_pos = 0
    for i, line in enumerate(lines):
        if line.startswith("import") or line.startswith("from"):
            insert_pos = i + 1

    for imp in REQUIRED_IMPORTS:
        if imp not in existing:
            lines.insert(insert_pos, imp)
            insert_pos += 1

    return "\n".join(lines)

def apply_replacements(text):
    for patt, repl in REPLACEMENTS.items():
        text = re.sub(patt, repl, text)
    return text

def migrate_file(path):
    txt = path.read_text(encoding="utf-8")
    original = txt

    # Apply replacements
    txt = apply_replacements(txt)

    # Add required imports
    txt = ensure_required_imports(txt)

    if txt != original:
        # Backup original
        BACKUP.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, BACKUP / path.name)

        # Overwrite file
        path.write_text(txt, encoding="utf-8")
        print(f"[UPDATED] {path.name}")
    else:
        print(f"[NO CHANGE] {path.name}")

def main():
    if not PAGES.exists():
        print("Pages folder not found:", PAGES)
        return

    for py in PAGES.glob("*.py"):
        migrate_file(py)

    print("\nMigration complete. Backup created at:", BACKUP)

if __name__ == "__main__":
    main()
