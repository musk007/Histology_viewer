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
import time
import uuid
import json
import pandas as pd
from concurrent.futures import ThreadPoolExecutor



from google.api_core.exceptions import NotFound


APP_VERSION = "2.0.0"

st.set_page_config(layout="wide")
st.markdown(
    """
    <style>
    div.stButton > button,
    div.stDownloadButton > button {
        height: auto !important;
        min-height: 2.6rem;
    }

    div.stButton > button p,
    div.stDownloadButton > button p {
        white-space: normal !important;
        overflow: visible !important;
        text-overflow: unset !important;
        line-height: 1.2 !important;
        text-align: center;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

left_logo, title_col, right_logo = st.columns([1.5, 5, 1.5])

with left_logo:
    st.image("assets/ADIA.png", width=300)

with title_col:
    st.markdown(
        """
        <h1 style="text-align: center; margin-top: 20px;">
            ReferralSeg Annotation Tool
        </h1>
        """,
        unsafe_allow_html=True
    )

with right_logo:
    st.image("assets/MBZUAI.png", width=450)

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

if "review_state" not in st.session_state:
    st.session_state.review_state = {}

def preserve_review_state():

    if "review_state" not in st.session_state:
        st.session_state.review_state = {}

    # First preserve temporary Tier 2 correction widgets
    for key in list(st.session_state.keys()):

        if key.startswith("_tier2_widget_"):

            permanent_key = key.replace(
                "_tier2_widget_",
                "",
                1
            )

            st.session_state[permanent_key] = (
                st.session_state[key]
            )

            st.session_state.review_state[permanent_key] = (
                st.session_state[key]
            )

    prefixes = (
        "quality_",
        "issue_tags_",
        "correction_",
        "histological_",
        "spatial_",
        "hierarchical_",
        "disambiguating_",
        "grounding_",
    )

    for key in list(st.session_state.keys()):
        if key.startswith(prefixes):
            st.session_state.review_state[key] = (
                st.session_state[key]
            )


def restore_review_state(case, selected_mask_file):
    if "review_state" not in st.session_state:
        return

    suffix = f"{case}_{selected_mask_file}"

    for key, value in st.session_state.review_state.items():
        if key.endswith(suffix) and key not in st.session_state:
            st.session_state[key] = value

def mark_unsaved():
    st.session_state.unsaved_changes = True

def preserve_tier1_text(case, selected_mask_file):
    st.session_state.unsaved_changes = True

    if "review_state" not in st.session_state:
        st.session_state.review_state = {}

    correction_key = f"correction_{case}_{selected_mask_file}"

    if correction_key in st.session_state:
        st.session_state.review_state[correction_key] = (
            st.session_state[correction_key]
        )



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

@st.cache_resource
def connect_to_in_progress_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scopes
    )

    client = gspread.authorize(creds)
    workbook = client.open("Histology_Feedback")

    return workbook.worksheet("In_Progress")

@st.cache_data(ttl=60)
def get_in_progress_row_map():
    sheet = connect_to_in_progress_sheet()
    rows = sheet.get_all_records()

    row_map = {}

    for row_number, row in enumerate(rows, start=2):
        reviewer_name = str(row.get("Reviewer", "")).strip()
        case_name = str(row.get("Case", "")).strip()
        mask_name = str(row.get("Mask", "")).strip()
        submission_type = str(
            row.get("Submission Type", "")
        ).strip()

        if reviewer_name and case_name and mask_name:
            row_map[
                (
                    reviewer_name,
                    case_name,
                    mask_name,
                    submission_type,
                )
            ] = row_number

    return row_map

@st.cache_data(ttl=60)
def get_in_progress_draft(
    reviewer,
    case,
    mask,
    submission_type,
):
    if reviewer is None:
        return None

    sheet = connect_to_in_progress_sheet()
    rows = sheet.get_all_records()

    matching_rows = [
        row
        for row in rows
        if (
            str(row.get("Reviewer", "")).strip() == reviewer
            and str(row.get("Case", "")).strip() == case
            and str(row.get("Mask", "")).strip() == mask
            and str(
                row.get("Submission Type", "")
            ).strip() == submission_type
        )
    ]

    if not matching_rows:
        return None

    return max(
        matching_rows,
        key=lambda row: str(next(iter(row.values()), ""))
    )

def rating_keyboard_shortcuts(component_key):

    components.html(
        """
        <script>
        const doc = window.parent.document;

        function isRatingGroup(group) {
            const labels = Array.from(
                group.querySelectorAll("label")
            );

            const texts = labels.map(
                label => label.innerText.trim()
            );

            return (
                texts.some(t => t.startsWith("1 —")) &&
                texts.some(t => t.startsWith("2 —")) &&
                texts.some(t => t.startsWith("3 —"))
            );
        }


        function handleRatingShortcut(event) {

            if (!["1", "2", "3"].includes(event.key)) {
                return;
            }


            const activeElement = doc.activeElement;

            const activeTag =
                activeElement?.tagName?.toLowerCase();

            const activeType =
                activeElement?.type?.toLowerCase();


            // Block shortcuts only while actually typing.
            // Radio buttons are allowed.
            if (
                activeTag === "textarea" ||
                activeTag === "select" ||
                (
                    activeTag === "input" &&
                    activeType !== "radio"
                )
            ) {
                return;
            }


            const radioGroups = Array.from(
                doc.querySelectorAll(
                    '[role="radiogroup"]'
                )
            ).filter(isRatingGroup);


            if (radioGroups.length === 0) {
                return;
            }


            let targetGroup = null;


            // ------------------------------------------
            // 1. Prefer the currently focused
            //    rating group.
            // ------------------------------------------

            if (activeElement) {

                const focusedGroup =
                    activeElement.closest?.(
                        '[role="radiogroup"]'
                    );

                if (
                    focusedGroup &&
                    isRatingGroup(focusedGroup)
                ) {
                    targetGroup = focusedGroup;
                }
            }


            // ------------------------------------------
            // 2. Otherwise use the last rating group
            //    the reviewer interacted with.
            // ------------------------------------------

            if (
                !targetGroup &&
                window.parent._lastRatingGroup &&
                doc.contains(
                    window.parent._lastRatingGroup
                )
            ) {
                targetGroup =
                    window.parent._lastRatingGroup;
            }


            // ------------------------------------------
            // 3. Otherwise choose the first unrated
            //    group.
            // ------------------------------------------

            if (!targetGroup) {

                targetGroup = radioGroups.find(
                    group =>
                        !group.querySelector(
                            'input[type="radio"]:checked'
                        )
                );
            }


            // ------------------------------------------
            // 4. Tier 1 / all Tier 2 groups already
            //    rated: fall back to first group.
            // ------------------------------------------

            if (!targetGroup) {
                targetGroup = radioGroups[0];
            }


            const targetLabel = Array.from(
                targetGroup.querySelectorAll("label")
            ).find(
                label =>
                    label.innerText
                        .trim()
                        .startsWith(
                            event.key + " —"
                        )
            );


            if (targetLabel) {

                window.parent._lastRatingGroup =
                    targetGroup;

                targetLabel.click();

                event.preventDefault();
            }
        }


        // Remember which rating row the reviewer
        // most recently clicked.
        function handleRatingClick(event) {

            const group =
                event.target.closest?.(
                    '[role="radiogroup"]'
                );

            if (
                group &&
                isRatingGroup(group)
            ) {
                window.parent._lastRatingGroup =
                    group;
            }
        }


        // Remove old handlers after Streamlit reruns
        if (window.parent._ratingShortcutHandler) {

            doc.removeEventListener(
                "keydown",
                window.parent._ratingShortcutHandler
            );
        }

        if (window.parent._ratingClickHandler) {

            doc.removeEventListener(
                "click",
                window.parent._ratingClickHandler
            );
        }


        window.parent._ratingShortcutHandler =
            handleRatingShortcut;

        window.parent._ratingClickHandler =
            handleRatingClick;


        doc.addEventListener(
            "keydown",
            window.parent._ratingShortcutHandler
        );

        doc.addEventListener(
            "click",
            window.parent._ratingClickHandler
        );

        </script>
        """,
        height=0,
    )


def delete_in_progress_draft(
    reviewer,
    case,
    selected_mask_file,
    tier,
):
    sheet = connect_to_in_progress_sheet()
    row_map = get_in_progress_row_map()

    rows_to_delete = []

    submission_type = (
        "Autosave"
        if tier == "Tier 1"
        else "Autosave Tier 2"
    )

    for submission_type in [submission_type]:
        row_number = row_map.get(
            (
                reviewer,
                case,
                selected_mask_file,
                submission_type,
            )
        )

        if row_number is not None:
            rows_to_delete.append(row_number)

    # Delete backwards so row numbers do not shift
    for row_number in sorted(
        rows_to_delete,
        reverse=True,
    ):
        sheet.delete_rows(row_number)

    get_in_progress_row_map.clear()
    get_in_progress_draft.clear()
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

def extract_organ(case_info):
    cancer_type = None

    for line in case_info.splitlines():
        line = line.strip()

        if line.lower().startswith("- cancer type"):
            cancer_type = line.split(":", 1)[1].strip()
            break

    if not cancer_type:
        return "Unknown"

    cancer_lower = cancer_type.lower()

    if "breast" in cancer_lower:
        return "Breast"

    if "colon" in cancer_lower or "colorectal" in cancer_lower:
        return "Colon"

    if "lung" in cancer_lower:
        return "Lung"

    if "prostate" in cancer_lower:
        return "Prostate"

    return "Unknown"



if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())

if "review_order" not in st.session_state:

    rng = np.random.default_rng(
        abs(hash(st.session_state.session_id)) % (2**32)
    )

    samples_by_organ = {
        "Breast": [],
        "Colon": [],
        "Lung": [],
        "Prostate": [],
        "Unknown": [],
    }

    for case_name in cases:
        case_dir = os.path.join("data", case_name)

        case_masks = sorted([
            f for f in os.listdir(case_dir)
            if f.startswith("mask_")
            and f.lower().endswith(
                (".png", ".jpg", ".jpeg", ".bmp")
            )
        ])

        for mask_file in case_masks:
            txt_file = os.path.join(
                case_dir,
                os.path.splitext(mask_file)[0] + ".txt"
            )

            if os.path.exists(txt_file):
                with open(txt_file, "r", encoding="utf-8") as f:
                    case_info = f.read()

                organ = extract_organ(case_info)
            else:
                organ = "Unknown"

            samples_by_organ.setdefault(
                organ,
                []
            ).append(
                (case_name, mask_file)
            )

    # Shuffle samples within each organ
    for organ in samples_by_organ:
        rng.shuffle(samples_by_organ[organ])

    organ_order = [
        "Breast",
        "Colon",
        "Lung",
        "Prostate",
    ]

    rng.shuffle(organ_order)

    review_order = []

    while any(
        samples_by_organ[organ]
        for organ in organ_order
    ):

        round_organ_order = organ_order.copy()
        rng.shuffle(round_organ_order)

        for organ in round_organ_order:
            if samples_by_organ[organ]:
                review_order.append(
                    samples_by_organ[organ].pop()
                )

    # Put unknown samples at the end
    rng.shuffle(samples_by_organ["Unknown"])
    review_order.extend(
        samples_by_organ["Unknown"]
    )

    st.session_state.review_order = review_order
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
    # Get already completed samples first
    sheet = connect_to_sheet()
    rows = sheet.get_all_records()

    reviewer_completed = {
        (
            str(row.get("Case", "")).strip(),
            str(row.get("Mask", "")).strip()
        )
        for row in rows
        if (
            str(row.get("Reviewer", "")).strip() == reviewer
            and str(row.get("Status", "")).strip().lower()
            in ["reviewed", "skipped"]
        )
    }

    # Resume only drafts that have NOT already been completed
    in_progress_sheet = connect_to_in_progress_sheet()
    in_progress_rows = in_progress_sheet.get_all_records()

    reviewer_drafts = [
        row for row in in_progress_rows
        if (
            str(row.get("Reviewer", "")).strip() == reviewer
            and str(row.get("Status", "")).strip().lower() == "in progress"
            and (
                str(row.get("Case", "")).strip(),
                str(row.get("Mask", "")).strip()
            ) not in reviewer_completed
        )
    ]

    found_resume = False

    if reviewer_drafts:
        # Use the most recently saved draft
        latest_draft = max(
            reviewer_drafts,
            key=lambda row: str(next(iter(row.values()), ""))
        )

        resume_case = str(
            latest_draft.get("Case", "")
        ).strip()

        resume_mask = str(
            latest_draft.get("Mask", "")
        ).strip()

        resume_submission_type = str(
            latest_draft.get("Submission Type", "")
        ).strip()

        resume_tier = (
            "Tier 2"
            if resume_submission_type == "Autosave Tier 2"
            else "Tier 1"
        )
        # Restore timing from the autosaved draft
        if "review_start_times" not in st.session_state:
            st.session_state.review_start_times = {}

        resume_sample_key = (
            f"{reviewer}_{resume_case}_{resume_mask}"
        )

        saved_started_at = str(
            latest_draft.get("Started At", "")
        ).strip()

        try:
            saved_active_seconds = float(
                latest_draft.get("Active Seconds", 0) or 0
            )
        except (TypeError, ValueError):
            saved_active_seconds = 0

        st.session_state.review_start_times[
            resume_sample_key
        ] = {
            "started_at": (
                saved_started_at
                if saved_started_at
                else datetime.now().isoformat()
            ),
            "start_time": time.time(),
            "accumulated_seconds": saved_active_seconds,
        }

        st.session_state[
            f"tier_{resume_case}_{resume_mask}"
        ] = resume_tier

        if resume_case in cases:
            resume_case_dir = os.path.join(
                "data",
                resume_case
            )

            resume_case_masks = sorted([
                f for f in os.listdir(resume_case_dir)
                if f.startswith("mask_")
                and f.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".bmp")
                )
            ])

            if resume_mask in resume_case_masks:
                st.session_state.case_index = cases.index(
                    resume_case
                )

                st.session_state.mask_index = (
                    resume_case_masks.index(resume_mask)
                )

                found_resume = True

    # --------------------------------------------------
    # 2. If no draft exists, use first unreviewed sample
    # --------------------------------------------------
    if not found_resume:
        sheet = connect_to_sheet()
        rows = sheet.get_all_records()

        reviewer_completed = {
            (
                str(row.get("Case", "")).strip(),
                str(row.get("Mask", "")).strip()
            )
            for row in rows
            if (
                str(row.get("Reviewer", "")).strip() == reviewer
                and str(row.get("Status", "")).strip().lower()
                in ["reviewed", "skipped"]
            )
        }

        for resume_case, resume_mask in st.session_state.review_order:

            if (
                resume_case,
                resume_mask
            ) not in reviewer_completed:

                resume_case_index = cases.index(
                    resume_case
                )

                resume_case_dir = os.path.join(
                    "data",
                    resume_case
                )

                resume_case_masks = sorted([
                    f for f in os.listdir(resume_case_dir)
                    if f.startswith("mask_")
                    and f.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".bmp")
                    )
                ])

                resume_mask_index = (
                    resume_case_masks.index(
                        resume_mask
                    )
                )

                st.session_state.case_index = (
                    resume_case_index
                )

                st.session_state.mask_index = (
                    resume_mask_index
                )

                found_resume = True
                break

    st.session_state.active_reviewer = reviewer
    st.session_state.unsaved_changes = False

    if found_resume:
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


if not mask_files:
    st.error(
        f"Case '{case}' contains no mask files. "
        "Use the sidebar to move to another case."
    )
    st.stop()

# keep index valid when case changes
st.session_state.mask_index = max(
    0,
    min(st.session_state.mask_index, len(mask_files) - 1)
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

# Tiles are served from GCS, so no local static/ lookup is needed here.

# image_url = f"{case}/{image_filename}"
# mask_url = f"{case}/{selected_mask_file}"
# image_dzi_url = f"http://localhost:8501/app/static/{case}/image_dzi.xml"




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

def upload_audio_to_gcs(
    audio_bytes,
    case,
    selected_mask_file,
    reviewer,
    version
):
    mask_name = os.path.splitext(selected_mask_file)[0]

    sample_id = f"{case}_{mask_name}"

    reviewer_id = (
        reviewer.strip()
        .lower()
        .replace(" ", "_")
    ) if reviewer else "unknown"

    filename = (
        f"{sample_id}_{reviewer_id}_v{version}.wav"
    )

    client = get_storage_client()

    bucket = client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(filename)

    blob.upload_from_string(
        audio_bytes,
        content_type="audio/wav"
    )

    return f"gs://{GCS_BUCKET_NAME}/{filename}"

def upload_review_json_to_gcs(review_record):

    reviewer_id = review_record["reviewer_id"]
    sample_id = review_record["sample_id"]
    version = review_record["version"]
    record_id = review_record["record_id"]

    filename = (
        f"review_records/{reviewer_id}/"
        f"{sample_id}_v{version}_{record_id}.json"
    )

    client = get_storage_client()

    bucket = client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(filename)

    blob.upload_from_string(
        json.dumps(
            review_record,
            indent=2,
            ensure_ascii=False
        ),
        content_type="application/json"
    )

    return f"gs://{GCS_BUCKET_NAME}/{filename}"


def append_review_jsonl_to_gcs(review_record):

    reviewer_id = review_record["reviewer_id"]

    filename = (
        f"review_records_jsonl/"
        f"{reviewer_id}.jsonl"
    )

    client = get_storage_client()
    bucket = client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(filename)

    try:
        existing_content = blob.download_as_text()
    except NotFound:
        existing_content = ""

    new_line = json.dumps(
        review_record,
        ensure_ascii=False
    )

    if existing_content and not existing_content.endswith("\n"):
        existing_content += "\n"

    updated_content = (
        existing_content
        + new_line
        + "\n"
    )

    blob.upload_from_string(
        updated_content,
        content_type="application/x-ndjson"
    )

    return f"gs://{GCS_BUCKET_NAME}/{filename}"

def load_reviewer_jsonl_records(reviewer):

    if reviewer is None:
        return []

    reviewer_id = (
        reviewer.strip()
        .lower()
        .replace(" ", "_")
    )

    filename = (
        f"review_records_jsonl/"
        f"{reviewer_id}.jsonl"
    )

    client = get_storage_client()
    bucket = client.bucket(GCS_BUCKET_NAME)
    blob = bucket.blob(filename)

    if not blob.exists():
        return []

    content = blob.download_as_text()

    records = []

    for line in content.splitlines():

        line = line.strip()

        if not line:
            continue

        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return records

def append_in_progress_review_jsonl(
    reviewer,
    case,
    selected_mask_file,
    tier,
    dimensions=None,
):
    if reviewer is None:
        return

    # ------------------------------------------
    # Sample metadata
    # ------------------------------------------
    mask_name = os.path.splitext(selected_mask_file)[0]
    sample_id = f"{case}_{mask_name}"

    reviewer_id = (
        reviewer.strip()
        .lower()
        .replace(" ", "_")
    )

    text_path = os.path.join(
        "data",
        case,
        f"{mask_name}.txt",
    )

    if os.path.exists(text_path):
        with open(text_path, "r", encoding="utf-8") as f:
            saved_case_info = f.read()
    else:
        saved_case_info = ""

    cancer_type, grade = extract_groundtruth(saved_case_info)
    organ = extract_organ(saved_case_info).lower()

    dataset_name = None

    for line in saved_case_info.splitlines():
        clean = line.strip()

        if clean.lower().startswith("- dataset"):
            dataset_name = clean.split(":", 1)[1].strip()
            break

    if not dataset_name:
        dataset_name = case.split("_", 1)[0].strip().lower()

    if not dataset_name:
        dataset_name = organ

    # ------------------------------------------
    # Mappings
    # ------------------------------------------
    rating_map = {
        "1 — Accurate": "accurate",
        "2 — Minor issues": "minor",
        "3 — Major issues": "major",
        "Accurate": "accurate",
        "Minor issues": "minor",
        "Major issues": "major",
    }

    tag_map = {
        "Terminology": "terminology",
        "Location": "location",
        "Morphology": "morphology",
        "Wrong structure": "wrong_structure",
        "Too vague": "too_vague",
        "Other": "other",
    }

    skip_reason_map = {
        "Image quality too poor": "image_quality",
        "Mask is wrong or ambiguous": "mask_wrong",
        "Outside my expertise": "outside_expertise",
        "Other": "other",
    }

    # ------------------------------------------
    # Tier 1 state
    # ------------------------------------------
    tier1_rating = st.session_state.get(
        f"quality_{case}_{selected_mask_file}"
    )

    tier1_tags = st.session_state.get(
        f"issue_tags_{case}_{selected_mask_file}",
        [],
    )

    tier1_correction = st.session_state.get(
        f"correction_{case}_{selected_mask_file}",
        "",
    )

    if tier == "Tier 1":
        overall = {
            "rating": rating_map.get(tier1_rating),
            "issue_tags": [
                tag_map.get(tag, tag)
                for tag in tier1_tags
            ],
            "correction": tier1_correction or None,
        }
    else:
        overall = {
            "rating": None,
            "issue_tags": [],
            "correction": None,
        }

    # ------------------------------------------
    # Tier 2 state
    # ------------------------------------------
    if dimensions is None:
        dimensions = extract_dimensions(saved_case_info)

    json_dimensions = {}

    for dim_key in [
        "histological",
        "spatial",
        "hierarchical",
        "disambiguating",
    ]:
        shown = dim_key in dimensions

        if tier == "Tier 2" and shown:
            rating = st.session_state.get(
                f"{dim_key}_rating_{case}_{selected_mask_file}"
            )

            tags = st.session_state.get(
                f"{dim_key}_tags_{case}_{selected_mask_file}",
                [],
            )

            correction = st.session_state.get(
                f"{dim_key}_correction_{case}_{selected_mask_file}",
                "",
            )

            json_dimensions[dim_key] = {
                "shown": True,
                "rating": rating_map.get(rating),
                "issue_tags": [
                    tag_map.get(tag, tag)
                    for tag in tags
                ],
                "correction": correction or None,
            }

        else:
            json_dimensions[dim_key] = {
                "shown": shown,
                "rating": None,
                "issue_tags": [],
                "correction": None,
            }

    grounding = None

    if tier == "Tier 2":
        saved_grounding = st.session_state.get(
            f"grounding_{case}_{selected_mask_file}"
        )

        grounding = (
            saved_grounding.lower()
            if saved_grounding
            else None
        )

    # ------------------------------------------
    # Shared state
    # ------------------------------------------
    skip_sample = st.session_state.get(
        f"skip_{case}_{selected_mask_file}",
        False,
    )

    skip_reason = st.session_state.get(
        f"skip_reason_{case}_{selected_mask_file}"
    )

    skip_note = st.session_state.get(
        f"skip_note_{case}_{selected_mask_file}",
        "",
    )

    flagged = st.session_state.get(
        f"flag_{case}_{selected_mask_file}",
        False,
    )

    free_text = st.session_state.get(
        f"feedback_{case}_{selected_mask_file}",
        "",
    )

    # ------------------------------------------
    # Timing
    # ------------------------------------------
    sample_key = f"{reviewer}_{case}_{selected_mask_file}"

    timing = st.session_state.get(
        "review_start_times",
        {},
    ).get(sample_key, {})

    started_at = timing.get(
        "started_at",
        datetime.now().isoformat(),
    )

    active_seconds = int(
        float(timing.get("accumulated_seconds", 0))
        + (
            time.time() - timing["start_time"]
            if "start_time" in timing
            else 0
        )
    )

    review_record = {
        "record_id": str(uuid.uuid4()),
        "sample_id": sample_id,
        "dataset": dataset_name,
        "organ": organ,

        "groundtruth": {
            "cancer_type": cancer_type,
            "grade_or_class": grade,
        },

        "reviewer_id": reviewer_id,
        "tier": 1 if tier == "Tier 1" else 2,
        "version": get_review_version(
            reviewer,
            case,
            selected_mask_file,
        ),

        "status": "in_progress",

        "overall": overall,
        "dimensions": json_dimensions,
        "grounding": grounding,

        "flagged": bool(flagged),

        "skip_reason": (
            skip_reason_map.get(skip_reason)
            if skip_sample
            else None
        ),

        "skip_note": (
            skip_note or None
            if skip_sample
            else None
        ),

        "voice_note_path": None,
        "free_text": free_text or None,

        "started_at": started_at,
        "submitted_at": None,
        "active_seconds": active_seconds,

        "session_id": st.session_state.session_id,
        "app_version": APP_VERSION,
    }

    append_review_jsonl_to_gcs(review_record)


def flatten_review_record(record):

    groundtruth = record.get("groundtruth", {}) or {}
    overall = record.get("overall", {}) or {}
    dimensions = record.get("dimensions", {}) or {}

    row = {
        "record_id": record.get("record_id", ""),
        "sample_id": record.get("sample_id", ""),
        "dataset": record.get("dataset", ""),
        "organ": record.get("organ", ""),

        "cancer_type": groundtruth.get("cancer_type", ""),
        "grade_or_class": groundtruth.get("grade_or_class", ""),

        "reviewer_id": record.get("reviewer_id", ""),
        "tier": record.get("tier", ""),
        "version": record.get("version", ""),
        "status": record.get("status", ""),

        "overall_rating": overall.get("rating", ""),
        "overall_issue_tags": "|".join(
            overall.get("issue_tags", []) or []
        ),
        "overall_correction": overall.get("correction", ""),

        "grounding": record.get("grounding", ""),
        "flagged": record.get("flagged", False),

        "skip_reason": record.get("skip_reason", ""),
        "skip_note": record.get("skip_note", ""),

        "voice_note_path": record.get("voice_note_path", ""),
        "free_text": record.get("free_text", ""),

        "started_at": record.get("started_at", ""),
        "submitted_at": record.get("submitted_at", ""),
        "active_seconds": record.get("active_seconds", ""),

        "session_id": record.get("session_id", ""),
        "app_version": record.get("app_version", ""),
    }

    for dim_key in [
        "histological",
        "spatial",
        "hierarchical",
        "disambiguating",
    ]:
        dim = dimensions.get(dim_key, {}) or {}

        row[f"{dim_key}_shown"] = dim.get(
            "shown",
            False
        )

        row[f"{dim_key}_rating"] = dim.get(
            "rating",
            ""
        )

        row[f"{dim_key}_tags"] = "|".join(
            dim.get("issue_tags", []) or []
        )

        row[f"{dim_key}_correction"] = dim.get(
            "correction",
            ""
        )

    return row


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

def autosave_in_progress(
    reviewer,
    case,
    selected_mask_file,
    quality_score,
    issue_tags,
    correction,
    feedback,
    skip_sample,
    skip_reason,
    skip_note,
    flagged,
):
    if reviewer is None:
        return

    sheet = connect_to_in_progress_sheet()
    sample_key = f"{reviewer}_{case}_{selected_mask_file}"

    timing = st.session_state.get(
        "review_start_times",
        {}
    ).get(sample_key, {})

    started_at = timing.get(
        "started_at",
        datetime.now().isoformat()
    )

    accumulated_seconds = float(
        timing.get("accumulated_seconds", 0)
    )

    current_session_seconds = (
        time.time() - timing["start_time"]
        if "start_time" in timing
        else 0
    )

    active_seconds = int(
        accumulated_seconds + current_session_seconds
    )

    row_data = [
        datetime.now().isoformat(),
        reviewer,
        case,
        selected_mask_file,
        quality_score or "",
        feedback or "",
        "No",
        "",
        "",
        "",
        "Autosave",
        "|".join(issue_tags),
        correction or "",
        "In Progress",
        skip_reason if skip_sample else "",
        skip_note if skip_sample else "",
        "Yes" if flagged else "No",
        "",
        started_at,
        active_seconds,
        ]

    row_map = get_in_progress_row_map()

    existing_row = row_map.get(
        (
            reviewer,
            case,
            selected_mask_file,
            "Autosave",
        )
    )

    if existing_row is not None:
        sheet.update(
            f"A{existing_row}:T{existing_row}",
            [row_data]
        )

    else:
        sheet.append_row(row_data)

    get_in_progress_row_map.clear()
    get_in_progress_draft.clear()


def autosave_current_sample(reviewer, case, selected_mask_file):
    if "review_state" not in st.session_state:
        st.session_state.review_state = {}

    tier1_keys = [
        f"quality_{case}_{selected_mask_file}",
        f"issue_tags_{case}_{selected_mask_file}",
        f"correction_{case}_{selected_mask_file}",
    ]

    for key in tier1_keys:
        if key in st.session_state:
            st.session_state.review_state[key] = st.session_state[key]

    if reviewer is None:
        return

    quality_key = f"quality_{case}_{selected_mask_file}"
    issue_key = f"issue_tags_{case}_{selected_mask_file}"
    correction_key = f"correction_{case}_{selected_mask_file}"
    feedback_key = f"feedback_{case}_{selected_mask_file}"
    skip_key = f"skip_{case}_{selected_mask_file}"
    skip_reason_key = f"skip_reason_{case}_{selected_mask_file}"
    skip_note_key = f"skip_note_{case}_{selected_mask_file}"
    flag_key = f"flag_{case}_{selected_mask_file}"

    autosave_in_progress(
        reviewer=reviewer,
        case=case,
        selected_mask_file=selected_mask_file,
        quality_score=st.session_state.get(quality_key),
        issue_tags=st.session_state.get(issue_key, []),
        correction=st.session_state.get(correction_key, ""),
        feedback=st.session_state.get(feedback_key, ""),
        skip_sample=st.session_state.get(skip_key, False),
        skip_reason=st.session_state.get(skip_reason_key),
        skip_note=st.session_state.get(skip_note_key, ""),
        flagged=st.session_state.get(flag_key, False),
    )
    

    st.session_state.unsaved_changes = True

def save_tier1_correction(
    reviewer,
    case,
    selected_mask_file,
):
    if "review_state" not in st.session_state:
        st.session_state.review_state = {}

    widget_key = (
        f"_tier1_widget_correction_"
        f"{case}_{selected_mask_file}"
    )

    correction_key = (
        f"correction_{case}_{selected_mask_file}"
    )

    value = st.session_state.get(widget_key, "")

    st.session_state[correction_key] = value
    st.session_state.review_state[correction_key] = value

    autosave_current_sample(
        reviewer,
        case,
        selected_mask_file,
    )


def autosave_tier2_sample(
    reviewer,
    case,
    selected_mask_file,
    dimensions,
):
    preserve_review_state()

    if reviewer is None:
        return

    sheet = connect_to_in_progress_sheet()
    sample_key = f"{reviewer}_{case}_{selected_mask_file}"

    timing = st.session_state.get(
        "review_start_times",
        {}
    ).get(sample_key, {})

    started_at = timing.get(
        "started_at",
        datetime.now().isoformat()
    )

    accumulated_seconds = float(
        timing.get("accumulated_seconds", 0)
    )

    current_session_seconds = (
        time.time() - timing["start_time"]
        if "start_time" in timing
        else 0
    )

    active_seconds = int(
        accumulated_seconds + current_session_seconds
    )

    row_map = get_in_progress_row_map()
    existing_row = row_map.get(
        (
            reviewer,
            case,
            selected_mask_file,
            "Autosave Tier 2",
        )
    )

    dim_values = {}

    for dim_key in dimensions.keys():

        rating_key = f"{dim_key}_rating_{case}_{selected_mask_file}"
        tags_key = f"{dim_key}_tags_{case}_{selected_mask_file}"
        correction_key = f"{dim_key}_correction_{case}_{selected_mask_file}"

        dim_values[dim_key] = {
            "rating": st.session_state.get(
                rating_key,
                st.session_state.review_state.get(rating_key)
            ),
            "tags": st.session_state.get(
                tags_key,
                st.session_state.review_state.get(tags_key, [])
            ),
            "correction": st.session_state.get(
                correction_key,
                st.session_state.review_state.get(correction_key, "")
            ),
        }

    grounding = st.session_state.get(
        f"grounding_{case}_{selected_mask_file}"
    )
    skip_sample = st.session_state.get(
        f"skip_{case}_{selected_mask_file}",
        False
    )

    skip_reason = st.session_state.get(
        f"skip_reason_{case}_{selected_mask_file}"
    )

    skip_note = st.session_state.get(
        f"skip_note_{case}_{selected_mask_file}",
        ""
    )

    flagged = st.session_state.get(
        f"flag_{case}_{selected_mask_file}",
        False
    )

    # For now, store Tier 2 state compactly in existing sheet fields
    feedback = st.session_state.get(
        f"feedback_{case}_{selected_mask_file}",
        ""
    )

    tier1_state = {
        "quality": st.session_state.get(
            f"quality_{case}_{selected_mask_file}"
        ),
        "issue_tags": st.session_state.get(
            f"issue_tags_{case}_{selected_mask_file}",
            [],
        ),
        "correction": st.session_state.get(
            f"correction_{case}_{selected_mask_file}",
            "",
        ),
    }

    tier2_payload = {
        "tier1": tier1_state,
        "dimensions": dim_values,
        "grounding": grounding,
        "feedback": feedback,
        "skip": skip_sample,
        "skip_reason": skip_reason,
        "skip_note": skip_note,
        "flagged": flagged,
    }

    import json

    row_data = [
        datetime.now().isoformat(),
        reviewer,
        case,
        selected_mask_file,
        "",
        json.dumps(tier2_payload),
        "No",
        "",
        "",
        "",
        "Autosave Tier 2",
        "",
        "",
        "In Progress",
        "",
        "",
        "No",
        "",
        started_at,
        active_seconds,
        ]

    if existing_row is not None:
        sheet.update(
            f"A{existing_row}:T{existing_row}",
            [row_data]
        )
    else:
        sheet.append_row(row_data)

    get_in_progress_row_map.clear()
    get_in_progress_draft.clear()

    

    st.session_state.unsaved_changes = True

def save_tier2_correction(
    reviewer,
    case,
    selected_mask_file,
    dimensions,
    dim_key,
):
    if "review_state" not in st.session_state:
        st.session_state.review_state = {}

    # Temporary Streamlit widget key
    widget_key = (
        f"_tier2_widget_{dim_key}_correction_"
        f"{case}_{selected_mask_file}"
    )

    # Permanent key used everywhere else in the app
    correction_key = (
        f"{dim_key}_correction_"
        f"{case}_{selected_mask_file}"
    )

    value = st.session_state.get(widget_key, "")

    # Store permanently
    st.session_state[correction_key] = value
    st.session_state.review_state[correction_key] = value

    # Then autosave all Tier 2 values
    autosave_tier2_sample(
        reviewer,
        case,
        selected_mask_file,
        dimensions,
    )

def save_tier2_tags(
    reviewer,
    case,
    selected_mask_file,
    dimensions,
    dim_key,
):
    if "review_state" not in st.session_state:
        st.session_state.review_state = {}

    widget_key = (
        f"_tier2_widget_{dim_key}_tags_"
        f"{case}_{selected_mask_file}"
    )

    tags_key = (
        f"{dim_key}_tags_"
        f"{case}_{selected_mask_file}"
    )

    value = st.session_state.get(widget_key, [])

    st.session_state[tags_key] = value
    st.session_state.review_state[tags_key] = value

    autosave_tier2_sample(
        reviewer,
        case,
        selected_mask_file,
        dimensions,
    )


@st.cache_data(ttl=300)
def get_completed_reviews():
    sheet = connect_to_sheet()
    rows = sheet.get_all_records()

    completed = set()

    for row in rows:
        reviewer_name = str(row.get("Reviewer", "")).strip()
        case_name = str(row.get("Case", "")).strip()
        mask_name = str(row.get("Mask", "")).strip()
        status = str(row.get("Status", "")).strip().lower()

        if (
            reviewer_name
            and case_name
            and mask_name
            and status in ["reviewed", "skipped"]
        ):
            completed.add(
                (reviewer_name, case_name, mask_name)
            )

    return completed



completed_reviews = get_completed_reviews()

@st.cache_data(ttl=300)
def get_review_version(reviewer_name, case_name, mask_name):
    sheet = connect_to_sheet()
    rows = sheet.get_all_records()

    versions = []

    for row in rows:
        if (
            str(row.get("Reviewer", "")).strip() == reviewer_name
            and str(row.get("Case", "")).strip() == case_name
            and str(row.get("Mask", "")).strip() == mask_name
        ):
            try:
                versions.append(int(row.get("Version", 0)))
            except (TypeError, ValueError):
                pass

    return max(versions, default=0) + 1

@st.cache_data(ttl=300)
def get_latest_review(reviewer_name, case_name, mask_name):
    if reviewer_name is None:
        return None

    sheet = connect_to_sheet()
    rows = sheet.get_all_records()

    matching_rows = [
        row for row in rows
        if (
            str(row.get("Reviewer", "")).strip() == reviewer_name
            and str(row.get("Case", "")).strip() == case_name
            and str(row.get("Mask", "")).strip() == mask_name
            and str(row.get("Status", "")).strip().lower()
            in ["reviewed", "skipped"]
        )
    ]

    if not matching_rows:
        return None

    # Rows are append-only, so the last matching row is the latest version
    return matching_rows[-1]


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
if "show_summary" not in st.session_state:
    st.session_state.show_summary = False

if st.sidebar.button(
    "Session Summary",
    disabled=reviewer is None,
    ):
    st.session_state.show_summary = True
    st.rerun()
    
def extract_groundtruth(case_info):
    cancer_type = ""
    grade = ""

    for line in case_info.splitlines():
        line = line.strip()

        if line.startswith("- Cancer Type"):
            cancer_type = line.split(":", 1)[1].strip()

        elif line.startswith("- Grade"):
            grade = line.split(":", 1)[1].strip()

    return cancer_type, grade



# The overlay is rendered by the GCS tile viewer, so no local blend is needed.

left, center, right = st.columns([0.5, 3.1, 1.9])


def autosave_shared_feedback(
    reviewer,
    case,
    selected_mask_file,
    tier,
    dimensions,
):
    if tier == "Tier 1":
        autosave_current_sample(
            reviewer,
            case,
            selected_mask_file,
        )
    else:
        autosave_tier2_sample(
            reviewer,
            case,
            selected_mask_file,
            dimensions,
        )

def autosave_shared_controls(
    reviewer,
    case,
    selected_mask_file,
    tier,
    dimensions,
):
    if tier == "Tier 1":
        autosave_current_sample(
            reviewer,
            case,
            selected_mask_file,
        )
    else:
        autosave_tier2_sample(
            reviewer,
            case,
            selected_mask_file,
            dimensions,
        )


def extract_dimensions(case_info):
    dimensions = {}
    current_dimension = None
    buffer = []

    mapping = {
        "Histological Reasoning": "histological",
        "Spatial Reasoning": "spatial",
        "Hierarchical Reasoning": "hierarchical",
        "Disambiguating Reasoning": "disambiguating",
    }

    for line in case_info.splitlines():
        clean = line.strip()

        if clean.startswith("#####"):
            title = clean.replace("#", "").strip()

            if current_dimension and buffer:
                dimensions[current_dimension] = " ".join(buffer).strip()

            current_dimension = mapping.get(title)
            buffer = []
            continue

        if current_dimension and clean:
            buffer.append(clean)

    if current_dimension and buffer:
        dimensions[current_dimension] = " ".join(buffer).strip()

    return dimensions

def handle_tier_change(
    reviewer,
    case,
    selected_mask_file,
    case_info,
):
    preserve_review_state()

    

def start_edit_review(
    reviewer,
    sample_key,
    case,
    selected_mask_file,
    latest_review,
):
    if latest_review:

        # --------------------------------------------------
        # Restore saved tier
        # --------------------------------------------------
        saved_tier = str(
            latest_review.get("Tier", "Tier 1")
        ).strip()

        if saved_tier in ["2", "Tier 2"]:
            saved_tier = "Tier 2"
        else:
            saved_tier = "Tier 1"

        st.session_state[
            f"tier_{case}_{selected_mask_file}"
        ] = saved_tier

        # --------------------------------------------------
        # Restore Tier 1
        # --------------------------------------------------
        if saved_tier == "Tier 1":

            saved_quality = str(
                latest_review.get("Quality", "")
            ).strip()

            quality_map = {
                "Accurate": "1 — Accurate",
                "Minor issues": "2 — Minor issues",
                "Major issues": "3 — Major issues",
            }

            if saved_quality in quality_map:
                st.session_state[
                    f"quality_{case}_{selected_mask_file}"
                ] = quality_map[saved_quality]

            saved_tags = str(
                latest_review.get("Issue Tags", "")
            ).strip()

            st.session_state[
                f"issue_tags_{case}_{selected_mask_file}"
            ] = (
                [tag for tag in saved_tags.split("|") if tag]
                if saved_tags
                else []
            )

            st.session_state[
                f"correction_{case}_{selected_mask_file}"
            ] = str(
                latest_review.get("Correction", "")
            )

        # --------------------------------------------------
        # Restore Tier 2
        # --------------------------------------------------
        elif saved_tier == "Tier 2":

            sample_id = (
                f"{case}_"
                f"{os.path.splitext(selected_mask_file)[0]}"
            )

            records = load_reviewer_jsonl_records(reviewer)

            matching_records = [
                record
                for record in records
                if (
                    record.get("sample_id") == sample_id
                    and int(record.get("tier", 0) or 0) == 2
                    and str(
                        record.get("status", "")
                    ).lower() in ["reviewed", "skipped"]
                )
            ]

            if matching_records:

                saved_record = max(
                    matching_records,
                    key=lambda record: int(
                        record.get("version", 0) or 0
                    ),
                )

                rating_map = {
                    "accurate": "1 — Accurate",
                    "minor": "2 — Minor issues",
                    "major": "3 — Major issues",
                }

                tag_map = {
                    "terminology": "Terminology",
                    "location": "Location",
                    "morphology": "Morphology",
                    "wrong_structure": "Wrong structure",
                    "too_vague": "Too vague",
                    "other": "Other",
                }

                saved_dimensions = (
                    saved_record.get("dimensions", {}) or {}
                )

                for dim_key, dim_data in saved_dimensions.items():

                    if not dim_data.get("shown", False):
                        continue

                    saved_rating = dim_data.get("rating")

                    if saved_rating in rating_map:
                        st.session_state[
                            f"{dim_key}_rating_"
                            f"{case}_{selected_mask_file}"
                        ] = rating_map[saved_rating]

                    st.session_state[
                        f"{dim_key}_tags_"
                        f"{case}_{selected_mask_file}"
                    ] = [
                        tag_map.get(tag, tag)
                        for tag in (
                            dim_data.get("issue_tags", []) or []
                        )
                    ]

                    saved_correction = (
                        dim_data.get("correction") or ""
                    )

                    correction_key = (
                        f"{dim_key}_correction_"
                        f"{case}_{selected_mask_file}"
                    )

                    widget_correction_key = (
                        f"_tier2_widget_{dim_key}_correction_"
                        f"{case}_{selected_mask_file}"
                    )

                    st.session_state[
                        correction_key
                    ] = saved_correction

                    st.session_state[
                        widget_correction_key
                    ] = saved_correction

                grounding_map = {
                    "yes": "Yes",
                    "partially": "Partially",
                    "no": "No",
                }

                saved_grounding = saved_record.get(
                    "grounding"
                )

                if saved_grounding in grounding_map:
                    st.session_state[
                        f"grounding_{case}_{selected_mask_file}"
                    ] = grounding_map[saved_grounding]

            # --------------------------------------------------
        # Restore shared saved fields
        # --------------------------------------------------
        sample_id = (
            f"{case}_"
            f"{os.path.splitext(selected_mask_file)[0]}"
        )

        target_tier = "1" if saved_tier == "Tier 1" else "2"

        all_records = load_reviewer_jsonl_records(reviewer)

        common_records = [
            record
            for record in all_records
            if (
                record.get("sample_id") == sample_id
                and str(record.get("tier", "")).strip()
                == target_tier
                and str(record.get("status", "")).lower()
                in ["reviewed", "skipped"]
            )
        ]

        if common_records:

            def record_version(record):
                try:
                    return int(record.get("version", 0) or 0)
                except (TypeError, ValueError):
                    return 0

            common_record = max(
                common_records,
                key=record_version,
            )

            # Preserve exact free text
            st.session_state[
                f"feedback_{case}_{selected_mask_file}"
            ] = common_record.get("free_text") or ""

            # Restore flag
            st.session_state[
                f"flag_{case}_{selected_mask_file}"
            ] = bool(
                common_record.get("flagged", False)
            )

            # Restore skip state
            is_skipped = (
                str(common_record.get("status", "")).lower()
                == "skipped"
            )

            st.session_state[
                f"skip_{case}_{selected_mask_file}"
            ] = is_skipped

            skip_reason_map = {
                "image_quality": "Image quality too poor",
                "mask_wrong": "Mask is wrong or ambiguous",
                "outside_expertise": "Outside my expertise",
                "other": "Other",
            }

            skip_reason_key = (
                f"skip_reason_{case}_{selected_mask_file}"
            )

            skip_note_key = (
                f"skip_note_{case}_{selected_mask_file}"
            )

            if is_skipped:
                saved_reason = common_record.get("skip_reason")

                if saved_reason in skip_reason_map:
                    st.session_state[
                        skip_reason_key
                    ] = skip_reason_map[saved_reason]

                st.session_state[
                    skip_note_key
                ] = common_record.get("skip_note") or ""

            else:
                st.session_state.pop(skip_reason_key, None)
                st.session_state.pop(skip_note_key, None)

        # Start timing the revision from the Edit click
        st.session_state.review_start_times[sample_key] = {
            "started_at": datetime.now().isoformat(),
            "start_time": time.time(),
        }

    st.session_state.edit_review[sample_key] = True

with left:
    st.subheader("Images")

    selected_view = st.radio(
        "Click to view",
        ["Overlay", "Original Image", "Mask"]
    )



with center:
    st.subheader(selected_view)

    mask_name = os.path.splitext(selected_mask_file)[0]

    if selected_view == "Original Image":
        viewer_url = (
            "https://storage.googleapis.com/"
            "histology-viewer-tiles-roba/static/viewer.html"
            f"?case={case}&view=image"
        )

    elif selected_view == "Mask":
        viewer_url = (
            "https://storage.googleapis.com/"
            "histology-viewer-tiles-roba/static/viewer.html"
            f"?case={case}&view=mask&mask={mask_name}"
        )

    else:  # Overlay
        viewer_url = (
            "https://storage.googleapis.com/"
            "histology-viewer-tiles-roba/static/viewer.html"
            f"?case={case}&view=overlay&mask={mask_name}"
        )

    components.iframe(
        viewer_url,
        height=780,
        scrolling=False,
    )


cancer_type, grade = extract_groundtruth(case_info)



with right:

    try:
        sample_number = st.session_state.review_order.index(
            (case, selected_mask_file)
        ) + 1
    except ValueError:
        sample_number = min(
            completed_masks + 1,
            total_masks
        )

    top_left, top_right = st.columns([2.2, 1])

    with top_left:
        st.markdown(
            (
                '<div style="display:flex; gap:8px;">'
                '<span style="background:#dceef8; padding:6px 12px; '
                'border-radius:16px; font-weight:600; font-size:14px;">'
                f'{cancer_type}'
                '</span>'
                '<span style="background:#eeeeee; padding:6px 12px; '
                'border-radius:16px; font-size:14px;">'
                f'{grade}'
                '</span>'
                '</div>'
            ),
            unsafe_allow_html=True,
        )

    with top_right:
        st.markdown(
            f"""
            <div style="
                text-align:right;
                padding-top:6px;
                color:#666;
            ">
                Sample {sample_number} of {total_masks}
            </div>
            """,
            unsafe_allow_html=True,
        )

    if total_masks > 0:
        st.progress(completed_masks / total_masks)

   
    

    # -------------------------
    # Instructions
    # -------------------------
    lines = case_info.splitlines()


    description_lines = []
    in_description = False

    for line in case_info.splitlines():
        clean_line = line.strip()

        if not clean_line:
            continue

        if clean_line.startswith("#### Description"):
            in_description = True
            continue

        if in_description:
            if clean_line.startswith("#####"):
                continue

            description_lines.append(clean_line)
    


    tier = st.radio(
        "Review mode",
        ["Tier 1", "Tier 2"],
        horizontal=True,
        key=f"tier_{case}_{selected_mask_file}",
        on_change=handle_tier_change,
        args=(
            reviewer,
            case,
            selected_mask_file,
            case_info,
        ),
        disabled=(
            reviewer is not None
            and (reviewer, case, selected_mask_file) in completed_reviews
            and not st.session_state.get(
                "edit_review",
                {}
            ).get(
                f"{reviewer}_{case}_{selected_mask_file}",
                False
            )
        )
    )

    restore_review_state(
        case,
        selected_mask_file
    )

    full_description = "\n\n".join(description_lines)

    if st.session_state.show_summary:

        st.markdown("## Session Summary")

        sheet = connect_to_sheet()
        rows = sheet.get_all_records()

        reviewer_rows = [
            row for row in rows
            if str(row.get("Reviewer", "")).strip() == reviewer
        ]

        # Keep only completed reviews
        completed_rows = [
            row for row in reviewer_rows
            if str(row.get("Status", "")).strip().lower()
            in ["reviewed", "skipped"]
        ]

        # Keep only the latest version of each sample
        latest_reviews = {}

        for row in completed_rows:
            key = (
                str(row.get("Case", "")).strip(),
                str(row.get("Mask", "")).strip()
            )

            try:
                version = int(row.get("Version", 0))
            except (TypeError, ValueError):
                version = 0

            if (
                key not in latest_reviews
                or version > latest_reviews[key]["version"]
            ):
                latest_reviews[key] = {
                    "version": version,
                    "row": row
                }

        latest_rows = [
            item["row"]
            for item in latest_reviews.values()
        ]

        reviewed_count = sum(
            str(row.get("Status", "")).strip().lower() == "reviewed"
            for row in latest_rows
        )

        skipped_count = sum(
            str(row.get("Status", "")).strip().lower() == "skipped"
            for row in latest_rows
        )

        flagged_count = sum(
            str(row.get("Flagged", "")).strip().lower() == "yes"
            for row in latest_rows
        )

        active_times = []

        for row in latest_rows:
            try:
                active_times.append(
                    float(row.get("Active Seconds", 0))
                )
            except (TypeError, ValueError):
                pass

        median_time = (
            np.median(active_times)
            if active_times
            else 0
        )

        summary_cols = st.columns(4)

        with summary_cols[0]:
            st.metric(
                "Reviewed",
                f"{reviewed_count} / {total_masks}"
            )

        with summary_cols[1]:
            st.metric(
                "Flagged",
                flagged_count
            )

        with summary_cols[2]:
            st.metric(
                "Skipped",
                skipped_count
            )

        with summary_cols[3]:
            st.metric(
                "Median time",
                f"{median_time:.0f} s"
            )

        st.markdown("### Progress by organ")

        organ_progress = {
            "Breast": {"completed": 0, "total": 0},
            "Colon": {"completed": 0, "total": 0},
            "Lung": {"completed": 0, "total": 0},
            "Prostate": {"completed": 0, "total": 0},
            "Unknown": {"completed": 0, "total": 0},
        }

        for case_name in cases:

            case_dir = os.path.join("data", case_name)

            case_masks = sorted([
                f for f in os.listdir(case_dir)
                if f.startswith("mask_")
                and f.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".bmp")
                )
            ])

            for mask_file in case_masks:

                # Read this sample's metadata to determine organ
                txt_file = os.path.join(
                    case_dir,
                    os.path.splitext(mask_file)[0] + ".txt"
                )

                if os.path.exists(txt_file):
                    with open(
                        txt_file,
                        "r",
                        encoding="utf-8"
                    ) as f:
                        sample_info = f.read()

                    organ = extract_organ(sample_info)

                else:
                    organ = "Unknown"

                if organ not in organ_progress:
                    organ_progress[organ] = {
                        "completed": 0,
                        "total": 0,
                    }

                organ_progress[organ]["total"] += 1

                if (
                    reviewer,
                    case_name,
                    mask_file
                ) in completed_reviews:

                    organ_progress[organ]["completed"] += 1


        # Display progress
        for organ in [
            "Breast",
            "Colon",
            "Lung",
            "Prostate",
            "Unknown",
        ]:

            stats = organ_progress[organ]

            # Do not display organs with no samples
            if stats["total"] == 0:
                continue

            st.write(
                f"**{organ}:** "
                f"{stats['completed']}/{stats['total']}"
            )

            st.progress(
                stats["completed"] / stats["total"]
            )

        # -------------------------
        # Flagged samples
        # -------------------------

        st.markdown("### Flagged samples")

        flagged_rows = [
            row
            for row in latest_rows
            if str(
                row.get("Flagged", "")
            ).strip().lower() == "yes"
        ]

        if flagged_rows:

            flagged_data = []

            for row in flagged_rows:
                flagged_data.append({
                    "Case": row.get("Case", ""),
                    "Mask": row.get("Mask", ""),
                    "Tier": row.get("Tier", ""),
                    "Quality": row.get("Quality", ""),
                    "Status": row.get("Status", ""),
                    "Submitted At": row.get(
                        "Submitted At",
                        ""
                    ),
                })

            flagged_df = pd.DataFrame(flagged_data)

            st.dataframe(
                flagged_df,
                use_container_width=True,
                hide_index=True,
            )

        else:
            st.info("No samples are currently flagged.")


        # -------------------------
        # Export
        # -------------------------

        st.markdown("### Export data")

        jsonl_records = load_reviewer_jsonl_records(reviewer)

        # Only final completed reviews belong in the progress CSV
        completed_records = [
            record
            for record in jsonl_records
            if str(record.get("status", "")).lower()
            in ["reviewed", "skipped"]
        ]

        # Keep only the highest version per sample/reviewer
        latest_records = {}

        for record in completed_records:

            key = (
                record.get("sample_id", ""),
                record.get("reviewer_id", ""),
            )

            try:
                version = int(record.get("version", 0))
            except (TypeError, ValueError):
                version = 0

            if (
                key not in latest_records
                or version > latest_records[key]["version"]
            ):
                latest_records[key] = {
                    "version": version,
                    "record": record,
                }

        export_rows = [
            flatten_review_record(item["record"])
            for item in latest_records.values()
        ]
        
        summary_nav_left, summary_nav_right = st.columns(2)

        if export_rows:

            export_df = pd.DataFrame(export_rows)

            export_df = export_df.sort_values(
                "sample_id"
            ).reset_index(drop=True)

            

            csv_data = export_df.to_csv(
                index=False
            ).encode("utf-8")

            with summary_nav_right:
                st.download_button(
                    label="Export progress CSV",
                    data=csv_data,
                    file_name=(
                        f"{reviewer.replace(' ', '_')}"
                        "_review_progress.csv"
                    ),
                    mime="text/csv",
                )

        else:
            st.info(
                "No completed reviews are available for export."
            )

        with summary_nav_left:
            if st.button(
                "Continue reviewing →",
                use_container_width=True,
            ):
                st.session_state.show_summary = False
                st.rerun()

        st.stop()

    
    

    already_reviewed = (
        reviewer is not None
        and (reviewer, case, selected_mask_file) in completed_reviews
    )

    # --------------------------------------------------
    # Reviewed state
    # --------------------------------------------------

    if "edit_review" not in st.session_state:
        st.session_state.edit_review = {}

    sample_key = f"{reviewer}_{case}_{selected_mask_file}"
    if "review_start_times" not in st.session_state:
        st.session_state.review_start_times = {}

    if sample_key not in st.session_state.review_start_times:
        st.session_state.review_start_times[sample_key] = {
            "started_at": datetime.now().isoformat(),
            "start_time": time.time(),
        }

    if sample_key not in st.session_state.edit_review:
        st.session_state.edit_review[sample_key] = False

    edit_mode = st.session_state.edit_review[sample_key]

    if already_reviewed and not edit_mode:

        latest_review = get_latest_review(
            reviewer,
            case,
            selected_mask_file
        )
        banner_col, edit_col = st.columns([4.5, 1])

        if latest_review:

            status = str(
                latest_review.get("Status", "Reviewed")
            ).strip()

            rating = str(
                latest_review.get("Quality", "")
            ).strip()

            submitted_at = str(
                latest_review.get("Submitted At", "")
            ).strip()

            version = str(
                latest_review.get("Version", "")
            ).strip()

            try:
                review_date = datetime.fromisoformat(
                    submitted_at
                ).strftime("%d %b %Y, %H:%M")
            except (ValueError, TypeError):
                review_date = submitted_at

            banner_text = f"✅ {status}"

            if rating:
                banner_text += f" · {rating}"

            if review_date:
                banner_text += f" · {review_date}"

            if version:
                banner_text += f" · Version {version}"



            with banner_col:
                st.markdown(
                    f"""
                    <div style="
                        background:#fff8e1;
                        border:1px solid #f0d98a;
                        border-radius:8px;
                        padding:12px 16px;
                        margin-bottom:10px;
                        color:#6b5a20;
                        font-weight:600;
                    ">
                        {banner_text}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            

        else:
            st.info("✅ This sample has already been reviewed.")

        with edit_col:
            st.button(
                "Edit review",
                key=f"edit_review_button_{sample_key}",
                use_container_width=True,
                on_click=start_edit_review,
                args=(
                    reviewer,
                    sample_key,
                    case,
                    selected_mask_file,
                    latest_review,
                ),
            )

        review_nav_left, review_nav_right = st.columns(2)

        with review_nav_left:
            if st.button(
                "Next unreviewed →",
                key=f"next_unreviewed_{sample_key}",
                use_container_width=True,
            ):
                found_next = False

                for next_case, next_mask in st.session_state.review_order:

                    if (
                        reviewer,
                        next_case,
                        next_mask
                    ) not in completed_reviews:

                        next_case_index = cases.index(next_case)

                        next_case_dir = os.path.join(
                            "data",
                            next_case
                        )

                        next_case_masks = sorted([
                            f for f in os.listdir(next_case_dir)
                            if f.startswith("mask_")
                            and f.lower().endswith(
                                (".png", ".jpg", ".jpeg", ".bmp")
                            )
                        ])

                        next_mask_index = next_case_masks.index(
                            next_mask
                        )

                        st.session_state.case_index = next_case_index
                        st.session_state.mask_index = next_mask_index

                        found_next = True
                        break

                if found_next:
                    st.rerun()
                else:
                    st.success(
                        "✅ All samples have been reviewed."
                    )

    elif already_reviewed and edit_mode:
        st.warning(
            "Editing this review will create a new version. "
            "The previous review will be preserved."
        )

    form_disabled = already_reviewed and not edit_mode
        

        # --------------------------------------------------
    # Load saved draft
    # --------------------------------------------------

    draft_submission_type = (
        "Autosave"
        if tier == "Tier 1"
        else "Autosave Tier 2"
    )

    draft_loaded_key = (
        f"draft_loaded_{tier}_{reviewer}_{case}_{selected_mask_file}"
    )

    draft = None

    if not st.session_state.get(draft_loaded_key, False):
        draft = get_in_progress_draft(
            reviewer,
            case,
            selected_mask_file,
            draft_submission_type,
        )

    # --------------------------------------------------
    # Restore Tier 1/common draft
    # --------------------------------------------------

    if (
        draft
        and not st.session_state.get(draft_loaded_key, False)
        and str(draft.get("Submission Type", "")).strip()
        != "Autosave Tier 2"
    ):

        quality_key = f"quality_{case}_{selected_mask_file}"
        issue_key = f"issue_tags_{case}_{selected_mask_file}"
        correction_key = f"correction_{case}_{selected_mask_file}"
        feedback_key = f"feedback_{case}_{selected_mask_file}"
        skip_key = f"skip_{case}_{selected_mask_file}"
        skip_reason_key = f"skip_reason_{case}_{selected_mask_file}"
        skip_note_key = f"skip_note_{case}_{selected_mask_file}"
        flag_key = f"flag_{case}_{selected_mask_file}"

        quality = str(draft.get("Quality", "")).strip()

        if quality:
            st.session_state[quality_key] = quality

        tags = str(draft.get("Issue Tags", "")).strip()

        if tags:
            st.session_state[issue_key] = [
                tag for tag in tags.split("|") if tag
            ]

        st.session_state[correction_key] = str(
            draft.get("Correction", "")
        )

        st.session_state[feedback_key] = str(
            draft.get("Feedback", "")
        )

        skip_reason = str(
            draft.get("Skip Reason", "")
        ).strip()

        st.session_state[skip_key] = bool(skip_reason)

        if skip_reason:
            st.session_state[skip_reason_key] = skip_reason

        st.session_state[skip_note_key] = str(
            draft.get("Skip Note", "")
        )

        st.session_state[flag_key] = (
            str(draft.get("Flagged", "")).strip().lower()
            == "yes"
        )

        st.session_state[draft_loaded_key] = True

    # If resuming Tier 2, also restore the Tier 1 answers
    tier1_draft_loaded_key = (
        f"draft_loaded_Tier 1_{reviewer}_{case}_{selected_mask_file}"
    )

    if (
        tier == "Tier 2"
        and not st.session_state.get(tier1_draft_loaded_key, False)
    ):
        tier1_draft = get_in_progress_draft(
            reviewer,
            case,
            selected_mask_file,
            "Autosave",
        )

        if tier1_draft:
            quality_key = f"quality_{case}_{selected_mask_file}"
            issue_key = f"issue_tags_{case}_{selected_mask_file}"
            correction_key = f"correction_{case}_{selected_mask_file}"

            quality = str(
                tier1_draft.get("Quality", "")
            ).strip()

            if quality:
                st.session_state[quality_key] = quality

            tags = str(
                tier1_draft.get("Issue Tags", "")
            ).strip()

            if tags:
                st.session_state[issue_key] = [
                    tag for tag in tags.split("|") if tag
                ]

            st.session_state[correction_key] = str(
                tier1_draft.get("Correction", "")
            )

            st.session_state[tier1_draft_loaded_key] = True


    # --------------------------------------------------
    # Restore Tier 2 draft
    # --------------------------------------------------

    if (
        draft
        and not st.session_state.get(draft_loaded_key, False)
        and str(draft.get("Submission Type", "")).strip()
        == "Autosave Tier 2"
    ):
        try:
            tier2_payload = json.loads(
                str(draft.get("Feedback", "")).strip()
            )

            saved_dimensions = tier2_payload.get(
                "dimensions",
                {}
            )

            saved_grounding = tier2_payload.get(
                "grounding"
            )
            saved_feedback = tier2_payload.get(
                "feedback",
                ""
            )
            saved_skip = tier2_payload.get(
                "skip",
                False
            )

            saved_skip_reason = tier2_payload.get(
                "skip_reason"
            )

            saved_skip_note = tier2_payload.get(
                "skip_note",
                ""
            )

            saved_flagged = tier2_payload.get(
                "flagged",
                False
            )

            for dim_key, dim_state in saved_dimensions.items():

                rating_key = (
                    f"{dim_key}_rating_"
                    f"{case}_{selected_mask_file}"
                )

                tags_key = (
                    f"{dim_key}_tags_"
                    f"{case}_{selected_mask_file}"
                )

                correction_key = (
                    f"{dim_key}_correction_"
                    f"{case}_{selected_mask_file}"
                )

                if dim_state.get("rating") is not None:
                    st.session_state[rating_key] = (
                        dim_state["rating"]
                    )

                if rating_key not in st.session_state:
                    st.session_state[rating_key] = (
                        st.session_state.review_state.get(
                            rating_key,
                            dim_state.get("rating")
                        )
                    )

                if tags_key not in st.session_state:
                    st.session_state[tags_key] = (
                        st.session_state.review_state.get(
                            tags_key,
                            dim_state.get("tags", [])
                        )
                    )

                if correction_key not in st.session_state:
                    st.session_state[correction_key] = (
                        st.session_state.review_state.get(
                            correction_key,
                            dim_state.get("correction", "")
                        )
                    )

            if saved_grounding is not None:
                st.session_state[
                    f"grounding_{case}_{selected_mask_file}"
                ] = saved_grounding

            feedback_key = f"feedback_{case}_{selected_mask_file}"

            st.session_state[feedback_key] = saved_feedback


            skip_key = f"skip_{case}_{selected_mask_file}"
            skip_reason_key = f"skip_reason_{case}_{selected_mask_file}"
            skip_note_key = f"skip_note_{case}_{selected_mask_file}"
            flag_key = f"flag_{case}_{selected_mask_file}"

            if skip_key not in st.session_state:
                st.session_state[skip_key] = saved_skip

            if skip_reason_key not in st.session_state:
                st.session_state[skip_reason_key] = saved_skip_reason

            if skip_note_key not in st.session_state:
                st.session_state[skip_note_key] = saved_skip_note

            if flag_key not in st.session_state:
                st.session_state[flag_key] = saved_flagged

            st.session_state[draft_loaded_key] = True

        except (json.JSONDecodeError, TypeError):
            pass


    if draft:
        st.caption("Draft restored from autosave.")


    # ==================================================
    # TIER 1
    # ==================================================

    if tier == "Tier 1":

 

        st.markdown(
            f"""
            <div style="
                font-size:16px;
                line-height:1.55;
                margin-bottom:6px;
            ">
                {full_description}
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.caption(
            "The full description is shown as one block. "
            "Review it against the highlighted region in the image panel."
        )

        quality_key = f"quality_{case}_{selected_mask_file}"

        

        quality_score_raw = st.radio(
            "Is this description clinically accurate for the highlighted region?",
            [
                "1 — Accurate",
                "2 — Minor issues",
                "3 — Major issues",
            ],
            index=None,
            horizontal=True,
            key=quality_key,
            disabled=form_disabled,
            on_change=autosave_current_sample,
            args=(reviewer, case, selected_mask_file),
        )

        RATING_MAP = {
            "1 — Accurate": "Accurate",
            "2 — Minor issues": "Minor issues",
            "3 — Major issues": "Major issues",
        }

        quality_score = (
            RATING_MAP.get(quality_score_raw, quality_score_raw)
            if quality_score_raw is not None
            else None
        )

        ISSUE_TAGS = [
            "Terminology",
            "Location",
            "Morphology",
            "Wrong structure",
            "Too vague",
            "Other",
        ]

        issue_tags = []
        correction = ""

        if quality_score in ["Minor issues", "Major issues"]:

            issue_key = f"issue_tags_{case}_{selected_mask_file}"
            issue_widget_key = f"_tier1_widget_issue_tags_{case}_{selected_mask_file}"

            if issue_widget_key not in st.session_state:
                st.session_state[issue_widget_key] = st.session_state.get(
                    issue_key,
                    st.session_state.review_state.get(issue_key, []),
                )

            issue_tags = st.multiselect(
                "What is the issue?",
                ISSUE_TAGS,
                key=issue_widget_key,
                disabled=form_disabled,
            )

            st.session_state[issue_key] = issue_tags
            st.session_state.review_state[issue_key] = issue_tags

            correction_key = f"correction_{case}_{selected_mask_file}"

            correction_widget_key = (
                f"_tier1_widget_correction_"
                f"{case}_{selected_mask_file}"
            )

            if correction_widget_key not in st.session_state:
                st.session_state[correction_widget_key] = st.session_state.get(
                    correction_key,
                    st.session_state.review_state.get(correction_key, ""),
                )

            st.text_input(
                "Optional correction",
                key=correction_widget_key,
                placeholder="Enter a corrected description if needed",
                disabled=form_disabled,
                on_change=save_tier1_correction,
                args=(
                    reviewer,
                    case,
                    selected_mask_file,
                ),
            )

            correction = st.session_state.get(
                correction_key,
                st.session_state.get(correction_widget_key, ""),
            )

        # These are Tier 2-only variables
        dimensions = {}
        dimension_ratings = {}
        dimension_tags = {}
        dimension_corrections = {}
        grounding = None


    # ==================================================
    # TIER 2
    # ==================================================

    else:

        dimensions = extract_dimensions(case_info)

        DIMENSION_LABELS = {
            "histological": "Histological Reasoning",
            "spatial": "Spatial Reasoning",
            "hierarchical": "Hierarchical Reasoning",
            "disambiguating": "Disambiguating Reasoning",
        }

        ISSUE_TAGS = [
            "Terminology",
            "Location",
            "Morphology",
            "Wrong structure",
            "Too vague",
            "Other",
        ]

        dimension_ratings = {}
        dimension_tags = {}
        dimension_corrections = {}

        # --------------------------------------------------
        # Tier 2 keyboard shortcuts
        # 1/2/3 apply to the first unrated dimension
        # --------------------------------------------------

        

        for dim_key, dim_text in dimensions.items():

            rating_key = (
                f"{dim_key}_rating_{case}_{selected_mask_file}"
            )

            current_rating = st.session_state.get(
                rating_key,
                st.session_state.review_state.get(rating_key)
            )

            issue_state = current_rating in [
                "2 — Minor issues",
                "3 — Major issues",
                "Minor issues",
                "Major issues",
            ]

            if issue_state:
                card_background = "#fff8e1"
                card_border = "#f0ad4e"
                border_width = "2px"
            else:
                card_background = "#fafafa"
                card_border = "#d9d9d9"
                border_width = "1px"

            raw_card_key = (
                f"tier2_card_{dim_key}_{case}_"
                f"{os.path.splitext(selected_mask_file)[0]}"
            )

            card_key = "".join(
                ch if ch.isalnum() or ch == "_"
                else "_"
                for ch in raw_card_key
            )

            st.markdown(
                f"""
                <style>
                .st-key-{card_key} {{
                    background-color: {card_background};
                    border: {border_width} solid {card_border};
                    border-radius: 10px;
                    padding: 14px;
                    margin-bottom: 12px;
                }}
                </style>
                """,
                unsafe_allow_html=True,
            )

            tags = []
            correction_text = ""

            with st.container(key=card_key):

                header_left, header_right = st.columns(
                    [1.2, 2.2]
                )

                with header_left:
                    st.markdown(
                        f"**{DIMENSION_LABELS[dim_key]}**"
                    )

                with header_right:
                    rating = st.radio(
                        f"{DIMENSION_LABELS[dim_key]} accuracy",
                        [
                            "1 — Accurate",
                            "2 — Minor issues",
                            "3 — Major issues",
                        ],
                        index=None,
                        horizontal=True,
                        key=rating_key,
                        disabled=form_disabled,
                        label_visibility="collapsed",
                        on_change=autosave_tier2_sample,
                        args=(
                            reviewer,
                            case,
                            selected_mask_file,
                            dimensions,
                        ),
                    )

                st.markdown(dim_text)

                RATING_MAP = {
                    "1 — Accurate": "Accurate",
                    "2 — Minor issues": "Minor issues",
                    "3 — Major issues": "Major issues",
                }

                rating_value = (
                    RATING_MAP.get(rating, rating)
                    if rating is not None
                    else None
                )

                dimension_ratings[dim_key] = rating_value

                if rating_value in [
                    "Minor issues",
                    "Major issues",
                ]:

                    st.markdown("**Issue details**")

                    tags_key = (
                        f"{dim_key}_tags_"
                        f"{case}_{selected_mask_file}"
                    )

                    widget_tags_key = (
                        f"_tier2_widget_{dim_key}_tags_"
                        f"{case}_{selected_mask_file}"
                    )

                    if widget_tags_key not in st.session_state:
                        st.session_state[widget_tags_key] = st.session_state.get(
                            tags_key,
                            st.session_state.review_state.get(tags_key, []),
                        )

                    tags = st.multiselect(
                        "What is the issue?",
                        ISSUE_TAGS,
                        key=widget_tags_key,
                        disabled=form_disabled,
                        on_change=save_tier2_tags,
                        args=(
                            reviewer,
                            case,
                            selected_mask_file,
                            dimensions,
                            dim_key,
                        ),
                    )

                    correction_key = (
                        f"{dim_key}_correction_"
                        f"{case}_{selected_mask_file}"
                    )

                    widget_correction_key = (
                        f"_tier2_widget_{dim_key}_correction_"
                        f"{case}_{selected_mask_file}"
                    )

                    if widget_correction_key not in st.session_state:
                        st.session_state[
                            widget_correction_key
                        ] = st.session_state.get(
                            correction_key,
                            st.session_state.review_state.get(
                                correction_key,
                                "",
                            ),
                        )

                    st.text_input(
                        "Optional correction",
                        key=widget_correction_key,
                        disabled=form_disabled,
                        on_change=save_tier2_correction,
                        args=(
                            reviewer,
                            case,
                            selected_mask_file,
                            dimensions,
                            dim_key,
                        ),
                    )

                    correction_text = st.session_state.get(
                        correction_key,
                        st.session_state.get(
                            widget_correction_key,
                            "",
                        ),
                    )

            dimension_tags[dim_key] = tags
            dimension_corrections[dim_key] = correction_text

        st.caption(
            "Only the dimensions present in this sample are shown. "
            "Each dimension is rated where it is read."
        )



        grounding = st.radio(
            "Does the description as a whole point to the highlighted region?",
            ["Yes", "Partially", "No"],
            index=None,
            horizontal=True,
            key=f"grounding_{case}_{selected_mask_file}",
            disabled=form_disabled,
            on_change=autosave_tier2_sample,
            args=(
                reviewer,
                case,
                selected_mask_file,
                dimensions,
            )
        )

        # Derive Tier 2 overall quality from the worst result
        severity = {
            "Accurate": 0,
            "Minor issues": 1,
            "Major issues": 2,
        }

        grounding_severity = {
            "Yes": 0,
            "Partially": 1,
            "No": 2,
        }

        scores = [
            severity[rating]
            for rating in dimension_ratings.values()
            if rating in severity
        ]

        if grounding in grounding_severity:
            scores.append(grounding_severity[grounding])

        if scores:
            worst_score = max(scores)

            quality_score = {
                0: "Accurate",
                1: "Minor issues",
                2: "Major issues",
            }[worst_score]
        else:
            quality_score = None

        issue_tags = []
        correction = ""



    feedback_key = f"feedback_{case}_{selected_mask_file}"

    if not form_disabled:
        rating_keyboard_shortcuts(
            f"shortcuts_{tier}_{case}_{selected_mask_file}"
        )

    feedback = st.session_state.get(
        feedback_key,
        ""
    )

    submit_disabled = already_reviewed and not edit_mode

    skip_key = f"skip_{case}_{selected_mask_file}"

    if skip_key not in st.session_state:
        st.session_state[skip_key] = False

    skip_sample = bool(st.session_state[skip_key])



    skip_reason = None
    skip_note = ""
    save_clicked = False
    audio_bytes = None

    flagged = st.session_state.get(
        f"flag_{case}_{selected_mask_file}",
        False
    )

    # ==================================================
    # NORMAL ACTION ROW
    # ==================================================
    if not skip_sample:

        action_save, action_audio, action_flag, action_skip = st.columns(
            [1.4, 1.4, 1.5, 1.4]
        )

        with action_save:
            save_clicked = st.button(
                "Save and next →",
                disabled=submit_disabled,
                type="primary",
                use_container_width=True,
            )

        with action_audio:
            if form_disabled:
                st.caption("Voice note disabled")
            else:
                audio_bytes = audio_recorder(
                    text="Voice note",
                    recording_color="#e74c3c",
                    neutral_color="#6aa36f",
                    icon_name="microphone",
                    icon_size="2x",
                    key=f"audio_{case}_{selected_mask_file}",
                )

        with action_flag:
            flagged = st.toggle(
                "🚩 Flag for discussion",
                key=f"flag_{case}_{selected_mask_file}",
                disabled=form_disabled,
                on_change=autosave_shared_controls,
                args=(
                    reviewer,
                    case,
                    selected_mask_file,
                    tier,
                    dimensions,
                ),
            )

        with action_skip:
            if st.button(
                "Skip /\n not assessable",
                key=f"open_skip_{case}_{selected_mask_file}",
                disabled=form_disabled,
                use_container_width=True,
            ):
                st.session_state[skip_key] = True
                st.rerun()

    # ==================================================
    # SKIP FLOW
    # ==================================================
    else:

        with st.container(border=True):

            st.markdown(
                "**Skip this sample.** "
                "Why is it not assessable? Pick one."
            )

            skip_reason = st.radio(
                "Skip reason",
                [
                    "Image quality too poor",
                    "Mask is wrong or ambiguous",
                    "Outside my expertise",
                    "Other",
                ],
                index=None,
                horizontal=True,
                key=f"skip_reason_{case}_{selected_mask_file}",
                disabled=form_disabled,
                label_visibility="collapsed",
                on_change=autosave_shared_controls,
                args=(
                    reviewer,
                    case,
                    selected_mask_file,
                    tier,
                    dimensions,
                ),
            )

            skip_note = st.text_input(
                "Optional note",
                key=f"skip_note_{case}_{selected_mask_file}",
                disabled=form_disabled,
                on_change=autosave_shared_controls,
                args=(
                    reviewer,
                    case,
                    selected_mask_file,
                    tier,
                    dimensions,
                ),
            )

        skip_confirm_col, skip_cancel_col, _ = st.columns(
            [1.8, 1.1, 2.5]
        )

        with skip_confirm_col:
            save_clicked = st.button(
                "Confirm skip and next →",
                disabled=submit_disabled,
                type="primary",
                use_container_width=True,
            )

        with skip_cancel_col:
            if st.button(
                "Cancel",
                key=f"cancel_skip_{case}_{selected_mask_file}",
                disabled=form_disabled,
                use_container_width=True,
            ):
                st.session_state[skip_key] = False

                autosave_shared_controls(
                    reviewer,
                    case,
                    selected_mask_file,
                    tier,
                    dimensions,
                )

                st.rerun()

    if save_clicked:
        try:
            # -------------------------
            # Reviewer validation
            # -------------------------
            if reviewer is None:
                st.error("Please select a reviewer before submitting.")
                st.stop()

            # -------------------------
            # Skip / rating validation
            # -------------------------
            if skip_sample:
                if skip_reason is None:
                    st.error("Please select a reason for skipping.")
                    st.stop()

            else:

                # ==================================================
                # Tier 1 validation
                # ==================================================

                if tier == "Tier 1":

                    if quality_score is None:
                        st.error(
                            "Please select a rating before submitting."
                        )
                        st.stop()

                    if (
                        quality_score == "Major issues"
                        and not issue_tags
                    ):
                        st.error(
                            "Please select at least one issue tag "
                            "for Major issues."
                        )
                        st.stop()


                # ==================================================
                # Tier 2 validation
                # Validate Tier 2 if:
                #   1. reviewer is currently in Tier 2, OR
                #   2. reviewer entered any Tier 2 data previously
                # ==================================================

                validation_dimensions = extract_dimensions(case_info)

                validation_rating_map = {
                    "1 — Accurate": "Accurate",
                    "2 — Minor issues": "Minor issues",
                    "3 — Major issues": "Major issues",
                }

                validation_labels = {
                    "histological": "Histological Reasoning",
                    "spatial": "Spatial Reasoning",
                    "hierarchical": "Hierarchical Reasoning",
                    "disambiguating": "Disambiguating Reasoning",
                }

                tier2_validation_data = {}

                for dim_key in validation_dimensions.keys():

                    rating_key = (
                        f"{dim_key}_rating_"
                        f"{case}_{selected_mask_file}"
                    )

                    tags_key = (
                        f"{dim_key}_tags_"
                        f"{case}_{selected_mask_file}"
                    )

                    correction_key = (
                        f"{dim_key}_correction_"
                        f"{case}_{selected_mask_file}"
                    )

                    rating_raw = st.session_state.get(
                        rating_key,
                        st.session_state.review_state.get(rating_key)
                    )

                    rating_value = (
                        validation_rating_map.get(
                            rating_raw,
                            rating_raw
                        )
                        if rating_raw is not None
                        else None
                    )

                    tags_value = st.session_state.get(
                        tags_key,
                        st.session_state.review_state.get(
                            tags_key,
                            []
                        )
                    )

                    correction_value = str(
                        st.session_state.get(
                            correction_key,
                            st.session_state.review_state.get(
                                correction_key,
                                ""
                            )
                        )
                    ).strip()

                    tier2_validation_data[dim_key] = {
                        "rating": rating_value,
                        "tags": tags_value,
                        "correction": correction_value,
                    }


                grounding_key = (
                    f"grounding_{case}_{selected_mask_file}"
                )

                grounding_value = st.session_state.get(
                    grounding_key,
                    st.session_state.review_state.get(
                        grounding_key
                    )
                )


                # Has the reviewer started Tier 2 at all?
                has_tier2_input = (
                    grounding_value is not None
                    or any(
                        data["rating"] is not None
                        or bool(data["tags"])
                        or bool(data["correction"])
                        for data in tier2_validation_data.values()
                    )
                )


                # Validate when currently using Tier 2,
                # OR when Tier 2 was previously started
                if tier == "Tier 2" or has_tier2_input:

                    missing_dimensions = [
                        dim_key
                        for dim_key, data
                        in tier2_validation_data.items()
                        if data["rating"] is None
                    ]

                    if missing_dimensions:

                        missing_names = [
                            validation_labels.get(
                                dim_key,
                                dim_key
                            )
                            for dim_key in missing_dimensions
                        ]

                        st.error(
                            "Please rate every Tier 2 reasoning "
                            "dimension before submitting. Missing: "
                            + ", ".join(missing_names)
                        )

                        st.stop()


                    for dim_key, data in tier2_validation_data.items():

                        if (
                            data["rating"] == "Major issues"
                            and not data["tags"]
                        ):

                            st.error(
                                "Please select at least one issue tag "
                                f"for {validation_labels.get(dim_key, dim_key)}."
                            )

                            st.stop()


                    if grounding_value is None:

                        st.error(
                            "Please answer the Tier 2 overall "
                            "grounding question before submitting."
                        )

                        st.stop()

            # -------------------------
            # Upload voice note
            # -------------------------
            sheet = connect_to_sheet()

            # Determine review version before uploading audio
            if already_reviewed:
                review_version = get_review_version(
                    reviewer,
                    case,
                    selected_mask_file
                )
            else:
                review_version = 1

            audio_link = ""

            if audio_bytes:
                audio_link = upload_audio_to_gcs(
                    audio_bytes,
                    case,
                    selected_mask_file,
                    reviewer,
                    review_version
                )

            # Tier 1 description is read-only
            original_instructions = full_description
            edited_instructions = full_description

            submission_type = (
                "Revision" if already_reviewed else "Initial"
            )
            timing = st.session_state.review_start_times[sample_key]

            started_at = timing["started_at"]
            submitted_at = datetime.now().isoformat()

            active_seconds = int(
                float(timing.get("accumulated_seconds", 0))
                + (time.time() - timing["start_time"])
            )
            # -------------------------
            # Final Tier 2 data
            # -------------------------

            # Always recover Tier 2 dimensions from the case,
            # regardless of which tier is currently being viewed
            all_dimensions = extract_dimensions(case_info)

            ALL_DIMENSION_KEYS = [
                "histological",
                "spatial",
                "hierarchical",
                "disambiguating",
            ]

            tier2_final_dimensions = {}

            for dim_key in ALL_DIMENSION_KEYS:
                rating_key = f"{dim_key}_rating_{case}_{selected_mask_file}"
                tags_key = f"{dim_key}_tags_{case}_{selected_mask_file}"
                correction_key = f"{dim_key}_correction_{case}_{selected_mask_file}"

                rating_raw = st.session_state.get(
                    rating_key,
                    st.session_state.review_state.get(rating_key)
                )

                rating_map = {
                    "1 — Accurate": "Accurate",
                    "2 — Minor issues": "Minor issues",
                    "3 — Major issues": "Major issues",
                }

                rating_final = (
                    rating_map.get(rating_raw)
                    if rating_raw is not None
                    else None
                )

                tags_final = st.session_state.get(
                    tags_key,
                    st.session_state.review_state.get(tags_key, [])
                )

                correction_final_dim = str(
                    st.session_state.get(
                        correction_key,
                        st.session_state.review_state.get(
                            correction_key,
                            ""
                        )
                    )
                ).strip()

                is_shown = dim_key in all_dimensions

                tier2_final_dimensions[dim_key] = {
                    "shown": is_shown,
                    "rating": rating_final if is_shown else None,
                    "tags": (
                        tags_final
                        if is_shown and rating_final in ["Minor issues", "Major issues"]
                        else []
                    ),
                    "correction": (
                        correction_final_dim
                        if is_shown and rating_final in ["Minor issues", "Major issues"]
                        else ""
                    ),
                }


            grounding_key = f"grounding_{case}_{selected_mask_file}"

            grounding_final = st.session_state.get(
                grounding_key,
                st.session_state.review_state.get(grounding_key)
            )

            has_tier2_data = (
                grounding_final is not None
                or any(
                    dim_data["shown"]
                    and (
                        dim_data["rating"] is not None
                        or dim_data["tags"]
                        or dim_data["correction"]
                    )
                    for dim_data in tier2_final_dimensions.values()
                )
            )

            if has_tier2_data:
                tier2_final_data = {
                    "dimensions": tier2_final_dimensions,
                    "grounding": grounding_final,
                }

                tier2_json = json.dumps(tier2_final_data)
            else:
                tier2_json = ""


            # -------------------------
            # Final feedback
            # -------------------------

            feedback_final = str(
                st.session_state.get(
                    f"feedback_{case}_{selected_mask_file}",
                    ""
                )
            ).strip()

            # Preserve the reviewer's actual free-text feedback
            free_text_final = feedback_final


            # -------------------------
            # Append Tier 2 corrections to feedback
            # -------------------------

            tier2_correction_lines = []

            DIMENSION_LABELS = {
                "histological": "Histological",
                "spatial": "Spatial",
                "hierarchical": "Hierarchical",
                "disambiguating": "Disambiguating",
            }

            for dim_key, dim_data in tier2_final_dimensions.items():

                correction_text = dim_data.get("correction", "").strip()

                if correction_text:
                    tier2_correction_lines.append(
                        f"{DIMENSION_LABELS.get(dim_key, dim_key)} correction: "
                        f"{correction_text}"
                    )

            if tier2_correction_lines:

                tier2_feedback = "\n".join(tier2_correction_lines)

                if feedback_final:
                    feedback_final += "\n\n" + tier2_feedback
                else:
                    feedback_final = tier2_feedback


            # -------------------------
            # Final Tier 1 correction
            # -------------------------

            correction_final = str(
                st.session_state.get(
                    f"correction_{case}_{selected_mask_file}",
                    ""
                )
            ).strip()


            # -------------------------
            # Saving resuts in a JSON file 
            # -------------------------
            # -------------------------
            # Structured JSON record
            # -------------------------

            record_id = str(uuid.uuid4())

            sample_mask_name = os.path.splitext(
                selected_mask_file
            )[0]

            sample_id = f"{case}_{sample_mask_name}"

            reviewer_id = (
                reviewer.strip()
                .lower()
                .replace(" ", "_")
            )

            organ = extract_organ(case_info).lower()


            # Dataset, if explicitly present in metadata
            dataset_name = None

            for line in case_info.splitlines():
                clean = line.strip()

                if clean.lower().startswith("- dataset"):
                    dataset_name = clean.split(":", 1)[1].strip()
                    break


            # Fallback when Dataset is not explicitly present in metadata
            if not dataset_name:
                dataset_name = case.split("_", 1)[0].strip().lower()

            if not dataset_name:
                dataset_name = organ

            RATING_JSON_MAP = {
                "1 — Accurate": "accurate",
                "2 — Minor issues": "minor",
                "3 — Major issues": "major",
                "Accurate": "accurate",
                "Minor issues": "minor",
                "Major issues": "major",
            }

            TAG_JSON_MAP = {
                "Terminology": "terminology",
                "Location": "location",
                "Morphology": "morphology",
                "Wrong structure": "wrong_structure",
                "Too vague": "too_vague",
                "Other": "other",
            }

            SKIP_REASON_MAP = {
                "Image quality too poor": "image_quality",
                "Mask is wrong or ambiguous": "mask_wrong",
                "Outside my expertise": "outside_expertise",
                "Other": "other",
            }


            # Tier 1 state
            tier1_rating_raw = st.session_state.get(
                f"quality_{case}_{selected_mask_file}",
                st.session_state.review_state.get(
                    f"quality_{case}_{selected_mask_file}"
                )
            )

            tier1_tags = st.session_state.get(
                f"issue_tags_{case}_{selected_mask_file}",
                st.session_state.review_state.get(
                    f"issue_tags_{case}_{selected_mask_file}",
                    []
                )
            )


            # Convert Tier 2 data to final schema
            json_dimensions = {}

            for dim_key, dim_data in tier2_final_dimensions.items():

                json_rating = RATING_JSON_MAP.get(
                    dim_data["rating"]
                )

                json_dimensions[dim_key] = {
                    "shown": dim_data["shown"],
                    "rating": json_rating,
                    "issue_tags": (
                        [
                            TAG_JSON_MAP.get(tag, tag)
                            for tag in dim_data["tags"]
                        ]
                        if json_rating in ["minor", "major"]
                        else []
                    ),
                    "correction": (
                        dim_data["correction"] or None
                        if json_rating in ["minor", "major"]
                        else None
                    ),
                }

            if tier == "Tier 1":

                tier1_json_rating = RATING_JSON_MAP.get(
                    tier1_rating_raw
                )

                json_overall = {
                    "rating": tier1_json_rating,
                    "issue_tags": (
                        [
                            TAG_JSON_MAP.get(tag, tag)
                            for tag in (tier1_tags or [])
                        ]
                        if tier1_json_rating in ["minor", "major"]
                        else []
                    ),
                    "correction": (
                        correction_final or None
                        if tier1_json_rating in ["minor", "major"]
                        else None
                    ),
                }

                # Keep only which dimensions were shown.
                # Tier 1 has no per-dimension ratings.
                json_dimensions = {
                    dim_key: {
                        "shown": dim_data["shown"],
                        "rating": None,
                        "issue_tags": [],
                        "correction": None,
                    }
                    for dim_key, dim_data in tier2_final_dimensions.items()
                }

                json_grounding = None

            else:

                json_overall = {
                    "rating": None,
                    "issue_tags": [],
                    "correction": None,
                }

                json_grounding = (
                    grounding_final.lower()
                    if grounding_final
                    else None
                )

            review_record = {

                "record_id": record_id,
                "sample_id": sample_id,
                "dataset": dataset_name,
                "organ": organ,

                "groundtruth": {
                    "cancer_type": cancer_type,
                    "grade_or_class": grade,
                },

                "reviewer_id": reviewer_id,

                "tier": 1 if tier == "Tier 1" else 2,
                "version": review_version,

                "status": (
                    "skipped"
                    if skip_sample
                    else "reviewed"
                ),

                "overall": json_overall,

                "dimensions": json_dimensions,

                "grounding": json_grounding,

                "flagged": bool(flagged),

                "skip_reason": (
                    SKIP_REASON_MAP.get(skip_reason)
                    if skip_sample
                    else None
                ),

                "skip_note": (
                    skip_note or None
                    if skip_sample
                    else None
                ),

                "voice_note_path": audio_link or None,

                "free_text": free_text_final or None,

                "started_at": started_at,
                "submitted_at": submitted_at,
                "active_seconds": active_seconds,

                "session_id": st.session_state.session_id,
                "app_version": APP_VERSION,
            }


           

            

           

            sheet_issue_tags = (
                issue_tags
                if (
                    not skip_sample
                    and tier == "Tier 1"
                    and quality_score in ["Minor issues", "Major issues"]
                )
                else []
            )

            sheet_correction = (
                correction_final
                if (
                    not skip_sample
                    and tier == "Tier 1"
                    and quality_score in ["Minor issues", "Major issues"]
                )
                else ""
            )
          

            # -------------------------
            # Save
            # -------------------------
            final_sheet_row = [
                datetime.now().isoformat(),
                reviewer,
                case,
                selected_mask_file,
                quality_score if not skip_sample else "",
                feedback_final,
                "Yes" if audio_bytes else "No",
                audio_link,
                original_instructions,
                edited_instructions,
                submission_type,
                "|".join(sheet_issue_tags),
                sheet_correction,
                "Skipped" if skip_sample else "Reviewed",
                skip_reason if skip_sample else "",
                skip_note if skip_sample else "",
                "Yes" if flagged else "No",
                review_version,
                started_at,
                submitted_at,
                active_seconds,
                st.session_state.session_id,
                APP_VERSION,
                tier,
                tier2_json,
            ]
            with ThreadPoolExecutor(max_workers=2) as executor:

                jsonl_future = executor.submit(
                    append_review_jsonl_to_gcs,
                    review_record,
                )

                sheet_future = executor.submit(
                    sheet.append_row,
                    final_sheet_row,
                )

                review_jsonl_path = jsonl_future.result()
                sheet_future.result()
          

       
            delete_in_progress_draft(
                reviewer,
                case,
                selected_mask_file,
                tier
            )
          

            
            st.session_state.edit_review[sample_key] = False

            st.session_state.unsaved_changes = False
            st.session_state.review_start_times.pop(sample_key, None)
            get_review_version.clear()
            get_latest_review.clear()
            

            if skip_sample:
                st.success("Sample skipped successfully.")
            else:
                st.success("Feedback submitted successfully.")

            get_completed_reviews.clear()
            completed_reviews.add(
                (reviewer, case, selected_mask_file)
            )

    

            # --------------------------------------------------
            # Move to next unreviewed sample in randomized order
            # --------------------------------------------------

            found_next = False

            for next_case, next_mask in st.session_state.review_order:

                if (
                    reviewer,
                    next_case,
                    next_mask
                ) not in completed_reviews:

                    next_case_index = cases.index(next_case)

                    next_case_dir = os.path.join(
                        "data",
                        next_case
                    )

                    next_case_masks = sorted([
                        f for f in os.listdir(next_case_dir)
                        if f.startswith("mask_")
                        and f.lower().endswith(
                            (".png", ".jpg", ".jpeg", ".bmp")
                        )
                    ])

                    next_mask_index = next_case_masks.index(
                        next_mask
                    )

                    st.session_state.case_index = next_case_index
                    st.session_state.mask_index = next_mask_index

                    found_next = True
                    break

            if found_next:
                st.rerun()
            else:
                st.success(
                    "✅ All samples have been reviewed. Thank you!"
                )
                

            

        except Exception as e:
            st.error(
                "Submission failed. The app will remain on the current sample."
            )
            st.exception(e)