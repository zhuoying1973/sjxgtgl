
import json
import os
from typing import Dict, Optional

class I18n:
    def __init__(self, locales_dir: str, default_lang: str = "zh"):
        self.locales_dir = locales_dir
        self.default_lang = default_lang
        self.translations: Dict[str, Dict[str, str]] = {}
        self._load_locales()

    def _load_locales(self):
        if not os.path.exists(self.locales_dir):
            os.makedirs(self.locales_dir, exist_ok=True)
            return

        for filename in os.listdir(self.locales_dir):
            if filename.endswith(".json"):
                lang = filename.split(".")[0]
                filepath = os.path.join(self.locales_dir, filename)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        self.translations[lang] = json.load(f)
                except Exception as e:
                    print(f"Failed to load locale {lang}: {e}")

    def get_text(self, key: str, lang: Optional[str] = None) -> str:
        lang = lang or self.default_lang
        
        # Fallback to default lang if requested lang not found
        if lang not in self.translations:
            lang = self.default_lang
            
        # If still not found (e.g. default lang file missing), return key
        if lang not in self.translations:
            return key

        return self.translations[lang].get(key, key)

    def reload(self):
        self.translations = {}
        self._load_locales()
