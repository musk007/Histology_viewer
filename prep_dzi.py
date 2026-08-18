import os
import subprocess
from PIL import Image
import numpy as np
import shutil



DATA_DIR = "data"
STATIC_DIR = "static"

extensions = [".png", ".jpg", ".jpeg", ".bmp"]

for case in os.listdir(DATA_DIR):
    case_path = os.path.join(DATA_DIR, case)

    if not os.path.isdir(case_path):
        continue

    static_case = os.path.join(STATIC_DIR, case)
    os.makedirs(static_case, exist_ok=True)

    # -------------------------
    # Original image
    # -------------------------
    image_path = None

    for ext in extensions:
        candidate = os.path.join(case_path, "image" + ext)

        if os.path.exists(candidate):
            image_path = candidate
            break

    if image_path is None:
        continue

    output_path = os.path.join(static_case, "image_dzi")

    subprocess.run([
        "vips",
        "dzsave",
        image_path,
        output_path
    ], check=True)

    # Load original image once
    image = Image.open(image_path).convert("RGB")
    image_np = np.array(image)

    # -------------------------
    # Masks + overlays
    # -------------------------
    for filename in os.listdir(case_path):
        if not filename.startswith("mask_"):
            continue

        if not filename.lower().endswith(tuple(extensions)):
            continue

        mask_path = os.path.join(case_path, filename)
        mask_name = os.path.splitext(filename)[0]

        # Mask DZI
        output_path = os.path.join(
            static_case,
            f"{mask_name}_dzi"
        )

        subprocess.run([
            "vips",
            "dzsave",
            mask_path,
            output_path
        ], check=True)

        # Overlay
        mask_img = Image.open(mask_path).convert("L")
        mask_np = np.array(mask_img)

        overlay_np = image_np.copy()
        overlay_np[mask_np > 0] = [255, 0, 0]

        overlay = (
            0.6 * image_np + 0.4 * overlay_np
        ).astype(np.uint8)

        overlay_img = Image.fromarray(overlay)

        overlay_filename = f"overlay_{mask_name}.png"
        overlay_path = os.path.join(static_case, overlay_filename)

        overlay_img.save(overlay_path)

        # Overlay DZI
        overlay_output = os.path.join(
            static_case,
            f"overlay_{mask_name}_dzi"
        )
        shutil.copy(
            os.path.join(static_case, "image_dzi.dzi"),
            os.path.join(static_case, "image_dzi.xml")
        )
        subprocess.run([
            "vips",
            "dzsave",
            overlay_path,
            overlay_output
        ], check=True)