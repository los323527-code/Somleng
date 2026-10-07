# សម្លេង — Telegram TTS Bot (Vercel webhook)

Bot បម្លែងអត្ថបទជា MP3 ដោយ Microsoft Edge TTS (លំនាំដើម៖ សំឡេងខ្មែរ `km-KH-PisethNeural`)។

```
Telegram → HTTPS webhook → Vercel (FastAPI) → Edge TTS → MP3 → Telegram
```

## 1. Deploy លើ Vercel

1. Push folder នេះទៅ GitHub។
2. Vercel → **Add New Project** → import repo (Vercel រក FastAPI ក្នុង `api/index.py` ដោយស្វ័យប្រវត្តិ)។
3. Settings → **Environment Variables**៖

| ឈ្មោះ | តម្លៃ | ចាំបាច់ |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | token ពី @BotFather | ✅ |
| `WEBHOOK_SECRET` | string ចៃដន្យ (`openssl rand -hex 24`) | ✅ (គ្មាន secret → webhook បដិសេធទាំងអស់) |
| `TTS_VOICE` | `km-KH-PisethNeural` | ជម្រើស |
| `ADMIN_IDS` | Telegram user ID, ញែកដោយ comma | ជម្រើស |

ផ្សេងៗទៀត (`TTS_MODEL`, `MAX_CHARS`, ...) មើល `.env.example`។

4. Deploy។

## 2. រក្សាទុកការកំណត់ (ណែនាំ — free)

Vercel រត់ជា function បណ្ដោះអាសន្ន ដូច្នេះបើគ្មាន storage ភាសា/សំឡេងដែលអ្នកប្រើជ្រើសនឹងបាត់ ហើយ admin នឹងទទួលសារ "អ្នកប្រើថ្មី" ដដែលៗ។

1. Vercel project → **Storage** (ឬ Marketplace) → បន្ថែម **Upstash Redis** (free plan)។
2. ភ្ជាប់ទៅ project នេះ — វាបន្ថែម `UPSTASH_REDIS_REST_URL` និង `UPSTASH_REDIS_REST_TOKEN` (ឬ `KV_REST_API_*`) ដោយស្វ័យប្រវត្តិ។
3. **Redeploy**។

ពិនិត្យ៖ បើក `https://YOUR-PROJECT.vercel.app/api/health` ត្រូវឃើញ `"persistent_state": true`។

> Bot ដំណើរការបានដែរបើគ្មាន Redis (state នៅក្នុង memory ប៉ុណ្ណោះ)។ ការកំណត់នឹងរក្សាទុកក្នុង key តែមួយសម្រាប់ stats/users ដូច្នេះសមសម្រាប់ bot តូច-មធ្យម។

## 3. ភ្ជាប់ webhook

លើកុំព្យូទ័រ/Termux បង្កើត `.env` (ចម្លងពី `.env.example`) ដាក់៖

```env
TELEGRAM_BOT_TOKEN=...
WEBHOOK_SECRET=...        # ដូចក្នុង Vercel
WEBHOOK_URL=https://YOUR-PROJECT.vercel.app
```

```bash
pip install httpx python-dotenv
python setup_webhook.py            # កំណត់ webhook → /api/webhook
python setup_webhook.py --info     # មើលស្ថានភាព (last_error_message ... )
python setup_webhook.py --delete   # លុប webhook (មុនប្រើ polling ក្នុងម៉ាស៊ីន)
```

រួចផ្ញើ `/start` ទៅ bot។

## 🎙 បម្លែងសំឡេងជាអត្ថបទ (Somleng STT)

អ្នកប្រើផ្ញើ **voice message** ឬ **ឯកសារ audio** មក bot → bot ឆ្លើយជាអត្ថបទខ្មែរ + ឯកសារ `subtitles.srt`។

1. បង្កើត token នៅ https://somlengsrt.com/api-tokens
2. Vercel → Environment Variables → `SOMLENG_API_TOKEN` = token របស់អ្នក → **Redeploy**
   (បើគ្មាន token bot នឹងឆ្លើយថាមុខងារនេះមិនទាន់បើក។)

របៀបដំណើរការ៖ `POST /transcribe` → រង់ចាំ `GET /files/{id}/status` រហូតដល់ចប់ (លំនាំដើម 30 វិនាទី) → `GET /files/{id}/srt`។
បើ audio វែងមិនទាន់ចប់ bot បង្ហាញប៊ូតុង **🔄 ពិនិត្យលទ្ធផល** (ត្រូវការ Redis ដើម្បីចាំ job ឈ្មោះ)។

- ដែនកំណត់ Telegram៖ bot ទាញឯកសារបានត្រឹម **20 MB**។
- Voice របស់ Telegram (OGG) ត្រូវបានបម្លែងជា MP3 ជាមុនដោយ ffmpeg (បិទបាន៖ `STT_CONVERT_MP3=0`)។
- ឯកសារ `somleng_stt.py` អានទ្រង់ទ្រាយ response បានច្រើនប្រភេទ ព្រោះឯកសារ API មិនបានបញ្ជាក់។ **បើ bot ឆ្លើយ "Unexpected API response"** សូមឱ្យ admin (`ADMIN_IDS`) សាកម្តងទៀត — admin នឹងឃើញ response ដើមខ្លះៗ ហើយអាចកែឈ្មោះ field (`ID_KEYS`, `STATUS_KEYS`) ក្នុង `somleng_stt.py`។ កុំ share token។

## ដំណោះស្រាយបញ្ហា

| រោគសញ្ញា | មូលហេតុ/ដំណោះស្រាយ |
|---|---|
| Bot មិនឆ្លើយ | `python setup_webhook.py --info` មើល `last_error_message`។ 403 = secret ខុស, 503 = មិនទាន់ដាក់ `WEBHOOK_SECRET` ក្នុង Vercel |
| ឆ្លើយតែម្តង រួចស្ងាត់ | មើល Vercel → Logs។ `Event loop`/import error → Redeploy |
| "មិនអាចបង្កើតសំឡេងបានទេ" | Edge TTS ជួនកាលដាច់ (bot សាកម្តងទៀតរួចហើយ)។ បន្ថយ `MAX_CHARS` |
| Timeout | `vercel.json` `maxDuration` = 60។ បើបើក Fluid Compute អាចបង្កើនដល់ 300 រួចបង្កើន `TTS_TIMEOUT_SECONDS` |

## Local (polling, សម្រាប់សាកល្បង)

```bash
pip install -r requirements.txt
cp .env.example .env     # ដាក់ token
python setup_webhook.py --delete   # polling មិនដំណើរការបើមាន webhook
python bot.py
```

Local polling រក្សាទុកការកំណត់ក្នុង `data/bot-state.pickle`។ Docker និង systemd (`Dockerfile`, `telegram-tts-bot.service`) នៅតែប្រើបានសម្រាប់ run 24/7 លើ VPS។

## Inworld TTS (ជម្រើស)

`pip install -r requirements-extra.txt` (ឬបន្ថែម `inworld-tts` ក្នុង requirements.txt) ហើយដាក់ `INWORLD_API_KEY`។ បើគ្មាន key ឬ package ម៉ូដែល Inworld នឹងលាក់ពីម៉ឺនុយ។

## សុវត្ថិភាព

- មិន commit `.env`។ កុំចែក `TELEGRAM_BOT_TOKEN` និង `WEBHOOK_SECRET`។
- បើ token ធ្លាយ → @BotFather → `/revoke`។
