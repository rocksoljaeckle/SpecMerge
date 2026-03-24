import streamlit as st

st.set_page_config(page_title="SpecMerge", page_icon=":material/merge:", layout="centered")

import time
import bisect
import fitz
import re
import asyncio
import tomli
from openai import AsyncOpenAI
import traceback
from markdown import markdown

from SpecMerge.pdf_utils import get_page_lines
from SpecMerge.pdf_editing import EditsList, PageEdit, StrikeThroughEdit, get_section_edits
from pdf_utils import multiple_split_insert

if 'config' not in st.session_state:
    with open('config.toml', 'rb') as f:
        st.session_state['config'] = tomli.load(f)

if 'global_config' not in st.session_state:
    with open('../GlobalUtils/config.toml', 'rb') as f:
        st.session_state['global_config'] = tomli.load(f)

def load_css():
    css_path = st.session_state['config'].get('css_path', 'assets/style.css')
    with open(css_path, 'r') as f:
        st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)

SECTION_NO_REGEX = r'^\d{3}\.\d{2}'

async def get_edited_doc(uploaded_file, status) -> tuple[fitz.Document, list[Exception], list[Exception]]:
    st.write("Parsing documents...")
    srcs_doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")
    srcs_sections_pages = get_srcs_sections_pages(srcs_doc)
    total_srcs_pages = srcs_doc.page_count
    srcs_sections_pages = fill_sections_pages(srcs_sections_pages, total_srcs_pages)

    specs_doc = fitz.open(st.session_state['config']['fp14_path'])
    specs_sections_pages = get_specs_sections_pages(specs_doc)
    total_specs_pages = specs_doc.page_count
    specs_sections_pages = fill_sections_pages(specs_sections_pages, total_specs_pages)

    st.write('Getting edits...')
    with open(st.session_state['config']['edit_prompt_path'], 'r', encoding='utf-8') as f:
        edit_prompt = f.read()
    openai_client = AsyncOpenAI(api_key=st.session_state['global_config']['openai_api_key'])
    semaphore = asyncio.Semaphore(10)  # limit concurrent requests to avoid rate limits
    edit_tasks = []
    for section_no in srcs_sections_pages.keys():
        edit_tasks.append(
            get_section_edits(
                section_no=section_no,
                specs_sections_pages=specs_sections_pages,
                srcs_sections_pages=srcs_sections_pages,
                specs_doc=specs_doc,
                srcs_doc=srcs_doc,
                edit_prompt=edit_prompt,
                openai_client=openai_client,
                sem=semaphore,
                model=st.session_state['config']['model']
            )
        )
    all_edits = await asyncio.gather(*edit_tasks, return_exceptions=True)

    st.write('Applying edits to document...')
    #collate edits by page
    page_edits = dict()
    edits_exceptions = []
    for section_no, edits in zip(specs_sections_pages.keys(), all_edits):
        if isinstance(edits, Exception):
            print(f'Error processing section {section_no}: {edits}')
            edits_exceptions.append(edits)
        else:
            for page, (insert_edits, strikethrough_edits) in edits.items():
                if page not in page_edits:
                    page_edits[page] = ([],
                                        [])  # list of (insert_edits, strikethrough_edits) for each section on this page
                page_edits[page][0].extend(insert_edits)
                page_edits[page][1].extend(strikethrough_edits)

    edited_doc = fitz.open()
    edited_doc.insert_pdf(specs_doc)  # start with original document, then apply edits
    insert_exceptions = []
    for page_no, (inserts, strikethroughs) in sorted(list(page_edits.items()), key=lambda x: x[0]):
        page = specs_doc.load_page(page_no)
        try:
            edited = multiple_split_insert(page, inserts, strikethroughs)
            edited_doc.delete_page(page_no)
            edited_doc.insert_pdf(edited, from_page=0, to_page=0, start_at=page_no)
        except Exception as e:
            print(f'Error processing page {page_no}: {e}')
            insert_exceptions.append(e)
    status.update(label="Merge complete!", state="complete")
    time.sleep(1) # ensure status update is seen before rerun
    return edited_doc, edits_exceptions, insert_exceptions

def get_specs_sections_pages(specs_doc: fitz.Document):
    specs_sections_pages = dict()
    section_no_regex = r'^\d{3}\.\d{2}'
    for page in specs_doc:
        blocks = page.get_text("dict")["blocks"]
        for block in blocks:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    flags = span["flags"]
                    # bit 4 (value 16) = bold
                    if flags & fitz.TEXT_FONT_BOLD:
                        match = re.match(section_no_regex, span["text"])
                        if match:
                            section_no = match.group(0)
                            if section_no not in specs_sections_pages:
                                specs_sections_pages[section_no] = []
                            specs_sections_pages[section_no].append(page.number)
    return specs_sections_pages

def get_srcs_sections_pages(srcs_doc: fitz.Document):
    return get_specs_sections_pages(srcs_doc) # todo - rewrite this with an ai bit to match disparate file structures

def fill_sections_pages(sections_pages: dict[str, list[int]], total_pages: int) -> dict[str, list[int]]:
    """Fill in intermediate pages between section start and the next section."""
    fill_page = total_pages - 1
    sort_func = lambda x: (min(x[1]), float(x[0]))
    for section_no, section_page_nos in sorted(sections_pages.items(), key=sort_func, reverse=True):
        # page_ranges.append( fill_page + 1 - min(page_nos) + 1)
        for page_no in range(min(section_page_nos) + 1, fill_page + 1):
            if page_no not in section_page_nos:
                sections_pages[section_no].append(page_no)
        fill_page = min(section_page_nos)
    return sections_pages

load_css()

st.title("SpecMerge")

if 'edited_doc_bytes' in st.session_state:
    st.success("Merge complete — your edited specification is ready.")

    st.divider()

    col_download, col_restart = st.columns([3, 1])
    with col_download:
        st.download_button(
            label=":material/download: Download Edited PDF",
            data=st.session_state['edited_doc_bytes'],
            file_name="edited_specification.pdf",
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )
    with col_restart:
        if st.button(":material/restart_alt: Start Over", type="secondary", use_container_width=True):
            del st.session_state['edited_doc_bytes']
            del st.session_state['edits_exceptions']
            del st.session_state['insert_exceptions']
            st.rerun()

    edit_errs = st.session_state.get('edits_exceptions', [])
    insert_errs = st.session_state.get('insert_exceptions', [])
    if edit_errs or insert_errs:
        st.divider()
        with st.expander(f":material/warning: {len(edit_errs) + len(insert_errs)} issue(s) encountered during merge", expanded=False):
            if edit_errs:
                st.markdown("**AI Edit Errors**")
                for e in edit_errs:
                    st.warning(f'{e}')
            if insert_errs:
                st.markdown("**PDF Insert Errors**")
                for e in insert_errs:
                    st.warning(f'{e}')

else:
    st.markdown(
        '<p class="app-description">Upload an SRCS (revisions) PDF to merge into the FP-14 specification document.</p>',
        unsafe_allow_html=True,
    )

    uploaded_file = st.file_uploader(
        "Upload SRCS PDF",
        type=["pdf"],
        accept_multiple_files=False,
        help="Select the SRCS revision document you want to merge into FP-14.",
    )

    st.button(
        ":material/play_arrow: Run SpecMerge",
        type="primary",
        disabled=uploaded_file is None,
        use_container_width=True,
        key="run_btn",
    )

    if st.session_state.get("run_btn"):
        # todo add in selection of specific fp doc
        with st.status("Merging specifications (may take several minutes)...", expanded=True) as status:
            edited_doc, edits_exceptions, insert_exceptions = asyncio.run(get_edited_doc(uploaded_file, status))
            st.session_state['edited_doc_bytes'] = edited_doc.tobytes()
            st.session_state['edits_exceptions'] = edits_exceptions
            st.session_state['insert_exceptions'] = insert_exceptions
            status.update(label="Merge complete!", state="complete")
        st.rerun()