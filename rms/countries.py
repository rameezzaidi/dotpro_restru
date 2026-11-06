"""Country presets: default timezone, currency and language per country.
Used by Branch / Settings forms (auto-fill) and by the seed command."""

COUNTRIES = {
    "PK": {"name": "Pakistan", "tz": "Asia/Karachi", "cur": "PKR", "sym": "Rs", "lang": "ur"},
    "IN": {"name": "India", "tz": "Asia/Kolkata", "cur": "INR", "sym": "₹", "lang": "en"},
    "BD": {"name": "Bangladesh", "tz": "Asia/Dhaka", "cur": "BDT", "sym": "৳", "lang": "en"},
    "AE": {"name": "United Arab Emirates", "tz": "Asia/Dubai", "cur": "AED", "sym": "AED", "lang": "ar"},
    "SA": {"name": "Saudi Arabia", "tz": "Asia/Riyadh", "cur": "SAR", "sym": "SAR", "lang": "ar"},
    "QA": {"name": "Qatar", "tz": "Asia/Qatar", "cur": "QAR", "sym": "QAR", "lang": "ar"},
    "KW": {"name": "Kuwait", "tz": "Asia/Kuwait", "cur": "KWD", "sym": "KWD", "lang": "ar"},
    "OM": {"name": "Oman", "tz": "Asia/Muscat", "cur": "OMR", "sym": "OMR", "lang": "ar"},
    "BH": {"name": "Bahrain", "tz": "Asia/Bahrain", "cur": "BHD", "sym": "BHD", "lang": "ar"},
    "EG": {"name": "Egypt", "tz": "Africa/Cairo", "cur": "EGP", "sym": "E£", "lang": "ar"},
    "TR": {"name": "Türkiye", "tz": "Europe/Istanbul", "cur": "TRY", "sym": "₺", "lang": "en"},
    "GB": {"name": "United Kingdom", "tz": "Europe/London", "cur": "GBP", "sym": "£", "lang": "en"},
    "US": {"name": "United States", "tz": "America/New_York", "cur": "USD", "sym": "$", "lang": "en"},
    "CA": {"name": "Canada", "tz": "America/Toronto", "cur": "CAD", "sym": "C$", "lang": "en"},
    "AU": {"name": "Australia", "tz": "Australia/Sydney", "cur": "AUD", "sym": "A$", "lang": "en"},
    "DE": {"name": "Germany", "tz": "Europe/Berlin", "cur": "EUR", "sym": "€", "lang": "en"},
    "FR": {"name": "France", "tz": "Europe/Paris", "cur": "EUR", "sym": "€", "lang": "en"},
    "ES": {"name": "Spain", "tz": "Europe/Madrid", "cur": "EUR", "sym": "€", "lang": "en"},
    "IT": {"name": "Italy", "tz": "Europe/Rome", "cur": "EUR", "sym": "€", "lang": "en"},
    "MY": {"name": "Malaysia", "tz": "Asia/Kuala_Lumpur", "cur": "MYR", "sym": "RM", "lang": "en"},
    "SG": {"name": "Singapore", "tz": "Asia/Singapore", "cur": "SGD", "sym": "S$", "lang": "en"},
    "ID": {"name": "Indonesia", "tz": "Asia/Jakarta", "cur": "IDR", "sym": "Rp", "lang": "en"},
    "ZA": {"name": "South Africa", "tz": "Africa/Johannesburg", "cur": "ZAR", "sym": "R", "lang": "en"},
    "NG": {"name": "Nigeria", "tz": "Africa/Lagos", "cur": "NGN", "sym": "₦", "lang": "en"},
    "KE": {"name": "Kenya", "tz": "Africa/Nairobi", "cur": "KES", "sym": "KSh", "lang": "en"},
}

COUNTRY_CHOICES = [(code, c["name"]) for code, c in sorted(COUNTRIES.items(), key=lambda x: x[1]["name"])]

LANGUAGES = {
    "en": {"name": "English", "dir": "ltr"},
    "ur": {"name": "اردو (Urdu)", "dir": "rtl"},
    "ar": {"name": "العربية (Arabic)", "dir": "rtl"},
}
LANGUAGE_CHOICES = [(k, v["name"]) for k, v in LANGUAGES.items()]


def timezone_choices():
    try:
        import zoneinfo
        zones = sorted(zoneinfo.available_timezones())
    except Exception:  # pragma: no cover
        zones = sorted({c["tz"] for c in COUNTRIES.values()})
    return [(z, z) for z in zones if "/" in z and not z.startswith(("Etc/", "SystemV/", "US/"))]
