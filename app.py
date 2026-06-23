import streamlit as st
from PIL import Image
import os
import numpy as np
import streamlit.components.v1 as components
import base64
from io import BytesIO
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

st.set_page_config(layout="wide")
st.title("Histopathology Dataset Viewer")

cases = sorted([
    d for d in os.listdir("data")
    if os.path.isdir(os.path.join("data", d))
])

case = st.sidebar.selectbox("Select case", cases)

case_path = os.path.join("data", case)

def find_image(folder, basename):
    extensions = [".png", ".jpg", ".jpeg", ".bmp"]

    for ext in extensions:
        path = os.path.join(folder, basename + ext)
        if os.path.exists(path):
            return path

    raise FileNotFoundError(
        f"Could not find {basename} with extensions {extensions}"
    )

image = Image.open(find_image(case_path, "image"))

mask_files = sorted([
    f for f in os.listdir(case_path)
    if f.startswith("mask_") and f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
])


selected_mask_file = st.sidebar.selectbox(
    "Select overlay",
    mask_files
)

mask = Image.open(os.path.join(case_path, selected_mask_file))

text_file = os.path.splitext(selected_mask_file)[0] + ".txt"
text_path = os.path.join(case_path, text_file)

if os.path.exists(text_path):
    with open(text_path, "r", encoding="utf-8") as f:
        case_info = f.read()
else:
    case_info = "No information available."


def create_overlay(image, mask, alpha=0.4):
    image = image.convert("RGB")
    mask = mask.convert("L")

    image_np = np.array(image)
    mask_np = np.array(mask)

    overlay_np = image_np.copy()
    overlay_np[mask_np > 0] = [255, 0, 0]

    blended = (
        (1 - alpha) * image_np + alpha * overlay_np
    ).astype(np.uint8)

    return Image.fromarray(blended)

def display_openseadragon(img):
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    img_base64 = base64.b64encode(buffer.getvalue()).decode()

    html_code = f"""
    <script src="https://cdnjs.cloudflare.com/ajax/libs/openseadragon/4.1.0/openseadragon.min.js"></script>

    <div id="openseadragon-viewer" style="width:100%; height:750px; border:1px solid #ddd;"></div>

    <script>
    var viewer = OpenSeadragon({{
        id: "openseadragon-viewer",
        prefixUrl: "https://cdnjs.cloudflare.com/ajax/libs/openseadragon/4.1.0/images/",
        tileSources: {{
            type: "image",
            url: "data:image/png;base64,{img_base64}"
        }},
        showNavigator: true,
        showHomeControl: true,
        gestureSettingsMouse: {{
            clickToZoom: true,
            dblClickToZoom: true,
            dragToPan: true,
            scrollToZoom: true
        }}
    }});
    </script>
    """

    components.html(html_code, height=780)


def connect_to_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = Credentials.from_service_account_file(
        "credentials.json",
        scopes=scopes
    )

    client = gspread.authorize(creds)
    sheet = client.open("Histology_Feedback").sheet1
    return sheet


overlay = create_overlay(image, mask)


left, center, right = st.columns([0.7, 3.2, 1.6])

with left:
    st.subheader("Images")
    selected_view = st.radio(
        "Click to view",
        ["Overlay", "Original Image", "Mask"]
    )

    st.image(image, caption="Original")
    st.image(mask, caption="Mask")

with center:
    st.subheader(selected_view)

    if selected_view == "Overlay":
        display_openseadragon(overlay)
    elif selected_view == "Original Image":
        display_openseadragon(image)
    else:
        display_openseadragon(mask)

with right:
    st.subheader("Information")

    sections = case_info.strip().split("\n\n")

    for section in sections:
        if ":" in section:
            title, text = section.split(":", 1)

            st.markdown(f"#### {title.strip()}")
            clean_text = text.strip().strip('"').replace("\n", " ")
            st.write(clean_text)

        else:
            st.write(section.strip())


    st.markdown("### Review checklist")
    st.markdown("""
    - Check image quality
    - Check segmentation accuracy
    - Check missed regions
    - Check over-segmentation
    - Check under-segmentation
    """)
    reviewer = st.text_input("Reviewer name")
    quality_score = st.radio(
        "Segmentation quality",
        ["Excellent", "Good", "Fair", "Poor"],
        horizontal=True
    )
    feedback = st.text_area(
        "Reviewer feedback",
        height=150,
        key=f"feedback_{case}_{selected_mask_file}"
    )

    if st.button("Submit feedback"):
        sheet = connect_to_sheet()

        sheet.append_row([
            datetime.now().isoformat(),
            reviewer,
            case,
            selected_mask_file,
            quality_score,
            feedback
        ])

        st.success("Feedback submitted.")