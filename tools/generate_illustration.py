#!/usr/bin/env python3
"""
Hillcourt Illustration & Asset Generator using Google Gemini 3 Pro Image.
"""
import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

def generate(prompt: str, output_path: str, model: str = "gemini-3-pro-image"):
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY is not set in .env", file=sys.stderr)
        sys.exit(1)

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    print(f"Generating image with {model}...")
    print(f"Prompt: {prompt[:120]}...")

    resp = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
        )
    )

    image_bytes = None
    for part in resp.candidates[0].content.parts:
        if part.inline_data:
            image_bytes = part.inline_data.data
            break

    if not image_bytes:
        print("ERROR: No image data returned from model.", file=sys.stderr)
        sys.exit(1)

    with open(out_file, "wb") as f:
        f.write(image_bytes)

    print(f"SUCCESS: Saved {len(image_bytes)} bytes to {out_file}")
    return str(out_file)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Hillcourt game illustrations and asset sheets")
    parser.add_argument("--prompt", required=True, help="Full text prompt")
    parser.add_argument("--out", required=True, help="Output file path (.png or .jpg)")
    parser.add_argument("--model", default="gemini-3-pro-image", help="Model name (default: gemini-3-pro-image)")
    args = parser.parse_args()

    generate(args.prompt, args.out, args.model)
