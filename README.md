# 🎭 AI Drama Generator

An AI-powered theatrical script generator built with Python, Flask, and OpenAI GPT.

Enter a dramatic premise, choose your characters and genre, and receive a fully
structured multi-scene drama script — ready to read, perform, or adapt.

---

## Features

- Generate dramatic scripts in multiple genres (tragedy, comedy, thriller, romance, …)
- Customisable characters and number of scenes
- Clean, theatre-inspired web UI
- Powered by OpenAI `gpt-4o-mini`

---

## Quick Start

### 1. Clone and install dependencies

```bash
git clone https://github.com/zhouzhoudi/ai-drama.git
cd ai-drama
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Set your OpenAI API key

```bash
export OPENAI_API_KEY="sk-..."
# or create a .env file:
echo "OPENAI_API_KEY=sk-..." > .env
```

### 3. Run the app

```bash
python app.py
```

Then open <http://127.0.0.1:5000> in your browser.

---

## Project Structure

```
ai-drama/
├── app.py               # Flask web application
├── drama_generator.py   # Core generation logic (calls OpenAI)
├── templates/
│   └── index.html       # Jinja2 HTML template
├── requirements.txt     # Python dependencies
└── README.md
```

---

## Environment Variables

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | **Required.** Your OpenAI API key. |

---

## License

MIT
