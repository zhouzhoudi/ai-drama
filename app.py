"""AI Drama Generator — Flask web application."""

import os

from dotenv import load_dotenv
from flask import Flask, render_template, request

from drama_generator import generate_drama

load_dotenv()

app = Flask(__name__)


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", drama=None, error=None, form={})


@app.route("/generate", methods=["POST"])
def generate():
    premise = request.form.get("premise", "").strip()
    characters_raw = request.form.get("characters", "").strip()
    genre = request.form.get("genre", "tragedy")
    num_scenes_raw = request.form.get("num_scenes", "3")

    form = {
        "premise": premise,
        "characters": characters_raw,
        "genre": genre,
        "num_scenes": num_scenes_raw,
    }

    if not premise:
        return render_template(
            "index.html",
            drama=None,
            error="Please enter a premise for your drama.",
            form=form,
        )

    try:
        num_scenes = max(1, min(6, int(num_scenes_raw)))
    except ValueError:
        num_scenes = 3

    characters = [c.strip() for c in characters_raw.split(",") if c.strip()]

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return render_template(
            "index.html",
            drama=None,
            error=(
                "OPENAI_API_KEY environment variable is not set. "
                "Please configure it before running the application."
            ),
            form=form,
        )

    try:
        drama = generate_drama(
            premise=premise,
            characters=characters,
            genre=genre,
            num_scenes=num_scenes,
            api_key=api_key,
        )
    except Exception as exc:  # noqa: BLE001
        return render_template(
            "index.html",
            drama=None,
            error=f"Drama generation failed: {exc}",
            form=form,
        )

    return render_template("index.html", drama=drama, error=None, form=form)


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug)
