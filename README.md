# daily_news_general

## Setup

### Environment variables

- `MAIL_FROM`: SMTP sender address
- `MAIL_TO`: recipient address
- `MAIL_PASSWORD`: SMTP password
- `OPENAI_API_KEY`: OpenAI API key for title translation
- `OPENAI_MODEL`: optional override for the translation model (default: `gpt-4o-mini`)
- `OPENAI_MAX_OUTPUT_TOKENS`: output token cap for translations (default: `512`)
- `OPENAI_TRANSLATION_BATCH_SIZE`: max titles per API call (default: `30`)
- `TITLE_TRANSLATION_CACHE_PATH`: cache file path (default: `data/title_translation_cache.json`)

### Translation cache

- Cache file: `data/title_translation_cache.json`
- Key: normalized title string (trim + collapse whitespace)
- The cache is loaded on each run and atomically updated after translation.

### Changing batch size / limits

- Increase or decrease `OPENAI_TRANSLATION_BATCH_SIZE` to control the number of titles per API call.
- Adjust `OPENAI_MAX_OUTPUT_TOKENS` to keep responses short and costs minimal.
