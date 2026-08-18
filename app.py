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
from audio_recorder_streamlit import audio_recorder
from google.cloud import storage

st.set_page_config(layout="wide")
logo_col1, logo_col2, spacer = st.columns([1, 1, 6])

with logo_col1:
    st.image("assets/MBZUAI.png", width=120)

with logo_col2:
    st.image("assets/ADIA.png", width=120)

st.title("Histopathology Dataset Viewer")

DRIVE_FOLDER_ID = "1m99hqkDM30woZKlLIpPfK5B6-nHTg-G2"
GCS_BUCKET_NAME = "histology-audio-feedback-roba"

REVIEWERS = [
    "Ayoub Nahal",
    "Shadab Khan",
    "Roba Al Majzoub",
]

# --------------------------------------------------
# Session state
# --------------------------------------------------

if "unsaved_changes" not in st.session_state:
    st.session_state.unsaved_changes = False

if "reviewer_name" not in st.session_state:
    st.session_state.reviewer_name = None

if "active_reviewer" not in st.session_state:
    st.session_state.active_reviewer = None

if "case_index" not in st.session_state:
    st.session_state.case_index = 0

if "mask_index" not in st.session_state:
    st.session_state.mask_index = 0


def mark_unsaved():
    st.session_state.unsaved_changes = True


# --------------------------------------------------
# Cases
# --------------------------------------------------

cases = sorted([
    d for d in os.listdir("data")
    if os.path.isdir(os.path.join("data", d))
])


# --------------------------------------------------
# Google Sheet connection
# --------------------------------------------------

@st.cache_resource
def connect_to_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scopes
    )

    client = gspread.authorize(creds)
    sheet = client.open("Histology_Feedback").sheet1

    return sheet


# --------------------------------------------------
# Find reviewer resume position
# --------------------------------------------------

@st.cache_data(ttl=300)
def get_resume_position(reviewer_name):
    if reviewer_name is None:
        return 0, 0

    sheet = connect_to_sheet()
    rows = sheet.get_all_records()

    reviewer_rows = [
        row for row in rows
        if str(row.get("Reviewer", "")).strip() == reviewer_name
    ]

    # New reviewer -> start at first sample
    if not reviewer_rows:
        return 0, 0

    completed = {
        (
            str(row.get("Case", "")).strip(),
            str(row.get("Mask", "")).strip()
        )
        for row in reviewer_rows
    }

    # Most recently submitted sample
    last_row = reviewer_rows[-1]

    last_case = str(last_row.get("Case", "")).strip()
    last_mask = str(last_row.get("Mask", "")).strip()

    if last_case not in cases:
        return 0, 0

    start_case_index = cases.index(last_case)

    # --------------------------------------------------
    # First search forward from the last reached sample
    # --------------------------------------------------

    for case_index in range(start_case_index, len(cases)):
        case_name = cases[case_index]
        case_dir = os.path.join("data", case_name)

        case_masks = sorted([
            f for f in os.listdir(case_dir)
            if f.startswith("mask_")
            and f.lower().endswith(
                (".png", ".jpg", ".jpeg", ".bmp")
            )
        ])

        start_mask_index = 0

        if case_name == last_case and last_mask in case_masks:
            start_mask_index = case_masks.index(last_mask) + 1

        for mask_index in range(start_mask_index, len(case_masks)):
            mask_file = case_masks[mask_index]

            if (case_name, mask_file) not in completed:
                return case_index, mask_index

    # --------------------------------------------------
    # If something earlier was skipped, find it
    # --------------------------------------------------

    for case_index, case_name in enumerate(cases):
        case_dir = os.path.join("data", case_name)

        case_masks = sorted([
            f for f in os.listdir(case_dir)
            if f.startswith("mask_")
            and f.lower().endswith(
                (".png", ".jpg", ".jpeg", ".bmp")
            )
        ])

        for mask_index, mask_file in enumerate(case_masks):
            if (case_name, mask_file) not in completed:
                return case_index, mask_index

    # Everything reviewed
    return len(cases) - 1, 0


# --------------------------------------------------
# Reviewer selection
# --------------------------------------------------

reviewer = st.selectbox(
    "Reviewer name",
    REVIEWERS,
    index=(
        REVIEWERS.index(st.session_state.reviewer_name)
        if st.session_state.reviewer_name in REVIEWERS
        else None
    ),
    placeholder="Select reviewer"
)

if reviewer is not None:
    st.session_state.reviewer_name = reviewer


# --------------------------------------------------
# Resume when reviewer is selected / changed
# --------------------------------------------------

if (
    reviewer is not None
    and reviewer != st.session_state.active_reviewer
):
    resume_case_index, resume_mask_index = get_resume_position(
        reviewer
    )

    st.session_state.case_index = resume_case_index
    st.session_state.mask_index = resume_mask_index
    st.session_state.active_reviewer = reviewer
    st.session_state.unsaved_changes = False

    st.rerun()


# --------------------------------------------------
# Current case
# --------------------------------------------------

case = cases[st.session_state.case_index]
case_path = os.path.join("data", case)


# --------------------------------------------------
# Sidebar warning
# --------------------------------------------------

if st.session_state.unsaved_changes:
    st.sidebar.error(
        "⚠️ You have unsaved changes. "
        "Submit before moving to another sample."
    )


# --------------------------------------------------
# Case navigation
# --------------------------------------------------

st.sidebar.write(
    f"Case {st.session_state.case_index + 1} / {len(cases)}"
)
st.sidebar.write(case)

case_prev_col, case_next_col = st.sidebar.columns(2)

with case_prev_col:
    if st.button("← Previous Case"):
        if st.session_state.case_index > 0:
            st.session_state.case_index -= 1
            st.session_state.mask_index = 0
            st.rerun()

with case_next_col:
    if st.button("Next Case →"):
        if st.session_state.case_index < len(cases) - 1:
            st.session_state.case_index += 1
            st.session_state.mask_index = 0
            st.rerun()

def find_image(folder, basename):
    extensions = [".png", ".jpg", ".jpeg", ".bmp"]

    for ext in extensions:
        path = os.path.join(folder, basename + ext)
        if os.path.exists(path):
            return path

    raise FileNotFoundError(
        f"Could not find {basename} with extensions {extensions}"
    )
@st.cache_data
def load_image(path):
    return Image.open(path).copy()

# image = Image.open(find_image(case_path, "image"))

mask_files = sorted([
    f for f in os.listdir(case_path)
    if f.startswith("mask_") and f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
])


if "mask_index" not in st.session_state:
    st.session_state.mask_index = 0

# keep index valid when case changes
st.session_state.mask_index = min(
    st.session_state.mask_index,
    len(mask_files) - 1
)

selected_mask_file = mask_files[st.session_state.mask_index]

st.sidebar.write(f"Mask {st.session_state.mask_index + 1} / {len(mask_files)}")

prev_col, next_col = st.sidebar.columns(2)

with prev_col:
    if st.button("← Previous"):
        if st.session_state.mask_index > 0:
            st.session_state.mask_index -= 1
            st.rerun()

with next_col:
    if st.button("Next →"):
        if st.session_state.mask_index < len(mask_files) - 1:
            st.session_state.mask_index += 1
            st.rerun()

st.sidebar.write(selected_mask_file)
static_case_path = os.path.join("static", case)

image_filename = os.path.basename(
    find_image(os.path.join("static", case), "image")
)

# image_url = f"{case}/{image_filename}"
# mask_url = f"{case}/{selected_mask_file}"
# image_dzi_url = f"http://localhost:8501/app/static/{case}/image_dzi.xml"


image = load_image(find_image(case_path, "image"))

mask = load_image(
    os.path.join(case_path, selected_mask_file)
)

text_file = os.path.splitext(selected_mask_file)[0] + ".txt"
text_path = os.path.join(case_path, text_file)

if os.path.exists(text_path):
    with open(text_path, "r", encoding="utf-8") as f:
        case_info = f.read()
else:
    case_info = "No information available."

def save_audio_file(audio_bytes, case, selected_mask_file, reviewer):
    os.makedirs("audio_feedback", exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mask_name = os.path.splitext(selected_mask_file)[0]
    reviewer_clean = reviewer.replace(" ", "_") if reviewer else "unknown"

    filename = f"{timestamp}_{reviewer_clean}_{case}_{mask_name}.wav"
    filepath = os.path.join("audio_feedback", filename)

    with open(filepath, "wb") as f:
        f.write(audio_bytes)

    return filepath
@st.cache_resource
def get_storage_client():
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"]
    )

    return storage.Client(
        credentials=creds,
        project=st.secrets["gcp_service_account"]["project_id"]
    )

def upload_audio_to_gcs(audio_bytes, case, selected_mask_file, reviewer):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    mask_name = os.path.splitext(selected_mask_file)[0]
    reviewer_clean = reviewer.replace(" ", "_") if reviewer else "unknown"

    filename = f"{timestamp}_{reviewer_clean}_{case}_{mask_name}.wav"

    client = get_storage_client()

    bucket = client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(filename)

    blob.upload_from_string(
        audio_bytes,
        content_type="audio/wav"
    )

    return f"gs://{GCS_BUCKET_NAME}/{filename}"

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

@st.cache_data
def image_to_base64(image_bytes):
    return base64.b64encode(image_bytes).decode()


def display_openseadragon(img):
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    img_base64 = base64.b64encode(buffer.getvalue()).decode()

    html_code = f"""
    <script src="https://cdnjs.cloudflare.com/ajax/libs/openseadragon/4.1.0/openseadragon.min.js"></script>

    <div id="openseadragon-viewer"
         style="width:100%; height:750px; border:1px solid #ddd;">
    </div>

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

def display_openseadragon_dzi(case):
    tile_url = f"http://127.0.0.1:8501/app/static/{case}/image_dzi_files/9/0_0.jpeg"

    html_code = f"""
    <div style="width:100%; height:750px; border:1px solid #ddd;">
        <p>Testing tile:</p>
        <p>{tile_url}</p>

        <img
            src="{tile_url}"
            style="max-width:500px; border:2px solid red;"
        >
    </div>
    """

    components.html(html_code, height=780)




@st.cache_data(ttl=300)
def get_completed_reviews():
    sheet = connect_to_sheet()
    rows = sheet.get_all_records()

    completed = set()

    for row in rows:
        reviewer_name = str(row.get("Reviewer", "")).strip()
        case_name = str(row.get("Case", "")).strip()
        mask_name = str(row.get("Mask", "")).strip()

        if reviewer_name and case_name and mask_name:
            completed.add(
                (reviewer_name, case_name, mask_name)
            )

    return completed



completed_reviews = get_completed_reviews()

st.sidebar.divider()
st.sidebar.subheader("Review Progress")

total_masks = 0
completed_masks = 0

for case_name in cases:
    case_dir = os.path.join("data", case_name)

    case_masks = sorted([
        f for f in os.listdir(case_dir)
        if f.startswith("mask_")
        and f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
    ])

    case_completed = sum(
        (reviewer, case_name, mask_file) in completed_reviews
        for mask_file in case_masks
    )

    total_masks += len(case_masks)
    completed_masks += case_completed

    if case_completed == len(case_masks):
        icon = "✅"
    elif case_completed > 0:
        icon = "🟡"
    else:
        icon = "⬜"

    st.sidebar.write(
        f"{icon} {case_name}: {case_completed}/{len(case_masks)}"
    )

if total_masks > 0:
    st.sidebar.progress(completed_masks / total_masks)
    st.sidebar.caption(
        f"{completed_masks}/{total_masks} samples reviewed"
    )


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

    mask_name = os.path.splitext(selected_mask_file)[0]

    if selected_view == "Original Image":
        viewer_url = (
            "https://storage.googleapis.com/histology-viewer-tiles-roba/static/viewer.html"
            f"?case={case}&view=image"
        )

    elif selected_view == "Mask":
        viewer_url = (
            "https://storage.googleapis.com/histology-viewer-tiles-roba/static/viewer.html"
            f"?case={case}&view=mask&mask={mask_name}"
        )

    else:  # Overlay
        viewer_url = (
            "https://storage.googleapis.com/histology-viewer-tiles-roba/static/viewer.html"
            f"?case={case}&view=overlay&mask={mask_name}"
        )

    st.iframe(
        viewer_url,
        height=780
    )

with right:
    st.subheader("Information")

    st.info(
        "Review only the currently selected highlighted region. "
        "Each mask represents one independent review sample."
    )

    lines = case_info.splitlines()

    original_sections = []
    edited_sections = []

    for i, line in enumerate(lines):
        line = line.strip()

        if not line:
            continue

        if line.startswith("#"):
            st.markdown(line)
            original_sections.append(line)
            edited_sections.append(line)
            continue

        original_sections.append(line)

        edited_text = st.text_area(
            "Edit text",
            value=line,
            height=100,
            key=f"edit_{case}_{selected_mask_file}_{i}",
            label_visibility="collapsed",
            on_change=mark_unsaved
        )

        edited_sections.append(edited_text)

        
    
    st.markdown("### Review checklist")
    st.markdown("""
    - Clarity & correctness of text
    - Accuracy of described morphology
    - Do instructions reflect intended reasoning dimension?
    - Any correction or notes to add
    """)
    

    already_reviewed = (
        reviewer is not None
        and (reviewer, case, selected_mask_file) in completed_reviews
    )

    if already_reviewed:
        st.warning("You have already reviewed this sample.")
    allow_resubmit = False

    if already_reviewed:
        allow_resubmit = st.checkbox(
            "I want to update/resubmit this review",
            key=f"resubmit_{reviewer}_{case}_{selected_mask_file}"
        )
        
    quality_score = st.radio(
        "Do you agree with the provided description?",
        ["Agree", "Disagree"],
        horizontal=True
    )
    if quality_score == "Disagree":
        st.warning(
            "Please correct the relevant instruction(s) above and optionally add feedback below."
        )
    feedback = st.text_area(
        "Text feedback",
        height=150,
        key=f"feedback_{case}_{selected_mask_file}",
        on_change=mark_unsaved
    )
    ## voice feedback
    st.markdown("### Voice Feedback")

    audio_bytes = audio_recorder(
        text="Click to record, then click to save the voicenote",
        recording_color="#e74c3c",
        neutral_color="#6aa36f",
        icon_name="microphone",
        icon_size="2x",
    )
    # original_instructions = "\n\n".join(original_sections)
    # edited_instructions = "\n\n".join(edited_sections)

    # has_unsaved_changes = (
    #     edited_instructions != original_instructions
    #     or feedback.strip() != ""
    #     or audio_bytes is not None
    # )
    # if has_unsaved_changes:
    #     st.sidebar.warning(
    #         "You have unsaved changes. Submit before moving to another sample."
    #     )

    submit_disabled = already_reviewed and not allow_resubmit

    if st.button(
        "Submit feedback",
        disabled=submit_disabled
    ):
        try:
            if reviewer is None:
                st.error("Please select a reviewer before submitting.")
                st.stop()

            sheet = connect_to_sheet()
            audio_link = ""

            if audio_bytes:
                audio_link = upload_audio_to_gcs(
                    audio_bytes,
                    case,
                    selected_mask_file,
                    reviewer
                )

            original_instructions = "\n\n".join(original_sections)
            edited_instructions = "\n\n".join(edited_sections)            

            submission_type = "Revision" if already_reviewed else "Initial"

            sheet.append_row([
                datetime.now().isoformat(),
                reviewer,
                case,
                selected_mask_file,
                quality_score,
                feedback,
                "Yes" if audio_bytes else "No",
                audio_link,
                original_instructions,
                edited_instructions,
                submission_type
            ])

            st.session_state.unsaved_changes = False

            st.success("Feedback submitted successfully.")

            get_completed_reviews.clear()
            completed_reviews = get_completed_reviews()

            # 1. Look for next unreviewed mask in current case
            next_mask_index = None

            for i in range(st.session_state.mask_index + 1, len(mask_files)):
                candidate_mask = mask_files[i]

                if (reviewer, case, candidate_mask) not in completed_reviews:
                    next_mask_index = i
                    break

            if next_mask_index is not None:
                st.session_state.mask_index = next_mask_index
                st.rerun()

            # 2. Move to next case with an unreviewed mask
            for next_case_index in range(
                st.session_state.case_index + 1,
                len(cases)
            ):
                next_case = cases[next_case_index]
                next_case_dir = os.path.join("data", next_case)

                next_case_masks = sorted([
                    f for f in os.listdir(next_case_dir)
                    if f.startswith("mask_")
                    and f.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".bmp")
                    )
                ])

                for mask_index, mask_file in enumerate(next_case_masks):
                    if (
                        reviewer,
                        next_case,
                        mask_file
                    ) not in completed_reviews:

                        st.session_state.case_index = next_case_index
                        st.session_state.mask_index = mask_index
                        st.rerun()

            all_reviewed = True

            for case_name in cases:
                case_dir = os.path.join("data", case_name)

                case_masks = sorted([
                    f for f in os.listdir(case_dir)
                    if f.startswith("mask_")
                    and f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp"))
                ])

                for mask_file in case_masks:
                    if (reviewer, case_name, mask_file) not in completed_reviews:
                        all_reviewed = False
                        break

                if not all_reviewed:
                    break

            if all_reviewed:
                st.success("✅ All samples have been reviewed. Thank you!")

            

        except Exception as e:
            st.error(
                "Submission failed. The app will remain on the current sample."
            )
            st.exception(e)
        