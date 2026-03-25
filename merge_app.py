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
import sys
import pickle

# #todo remove
# from pdf_utils import multiple_split_edits, StrikethroughEdit, InsertEdit
# with open('tests/page_edits_dump.pkl', 'wb') as f:
#     debug_page_edits = {}
#     for page, (strikethroughs, inserts) in st.session_state['debug_page_edits'].items():
#         debug_page_edits[page] = (
#             [StrikethroughEdit.model_validate(e.model_dump()) for e in strikethroughs],
#             [InsertEdit.model_validate(e.model_dump()) for e in inserts]
#         )
#     pickle.dump(debug_page_edits, f)
# #end rm

DEV_MODE = True# todo set false/rm
if DEV_MODE:
    for mod_name in ['pdf_utils', 'llm_pdf_editing']:
        if mod_name in sys.modules.keys():
            del sys.modules[mod_name]

from pdf_utils import multiple_split_edits, StrikethroughEdit, InsertEdit
from llm_pdf_editing import get_section_edits



if 'config' not in st.session_state:
    with open('config.toml', 'rb') as f:
        st.session_state['config'] = tomli.load(f)

def load_css():
    css_path = st.session_state['config'].get('css_path', 'assets/style.css')
    with open(css_path, 'r') as f:
        st.html(f'<style>{f.read()}</style>')

SECTION_NO_REGEX = r'^\d{3}\.\d{2}'

async def get_edited_doc(specs_doc: fitz.Document, srcs_doc: fitz.Document) -> tuple[fitz.Document, list[Exception], list[Exception]]:
    parsing_start = time.time()
    with st.spinner("Parsing documents...", show_time=True):
        srcs_sections_pages = get_srcs_sections_pages(srcs_doc)
        total_srcs_pages = srcs_doc.page_count
        srcs_sections_pages = fill_sections_pages(srcs_sections_pages, total_srcs_pages)

        specs_sections_pages = get_specs_sections_pages(specs_doc)
        total_specs_pages = specs_doc.page_count
        specs_sections_pages = fill_sections_pages(specs_sections_pages, total_specs_pages)
    parsing_seconds = time.time() - parsing_start
    st.write(f'✅ Documents parsed in {parsing_seconds//60} minutes {parsing_seconds%60:.1f} seconds')


    editing_start = time.time()
    with st.spinner('Getting edits...', show_time = True):
        with open(st.session_state['config']['edit_prompt_path'], 'r', encoding='utf-8') as f:
            edit_prompt = f.read()
        openai_client = AsyncOpenAI(api_key=st.secrets['openai_api_key'])
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
    editing_seconds = time.time() - editing_start
    st.write(f'✅ Edits retrived in {int(editing_seconds//60)} minutes {editing_seconds%60:.1f} seconds')


    edit_apply_start = time.time()
    with st.spinner('Applying edits to document...', show_time = True):
        #collate edits by page
        page_edits = dict()
        edits_exceptions = []
        for section_no, edits in zip(srcs_sections_pages.keys(), all_edits):
            if isinstance(edits, Exception):
                print(f'Error processing section {section_no}: {edits}')
                edits_exceptions.append((section_no, edits))
            else:
                for page, (strikethrough_edits, insert_edits) in edits.items():
                    if page not in page_edits:
                        page_edits[page] = ([], [])
                    page_edits[page][0].extend(strikethrough_edits)
                    page_edits[page][1].extend(insert_edits)

        # #todo remove
        # st.session_state['debug_page_edits'] = page_edits

        edited_doc = fitz.open()
        edited_doc.insert_pdf(specs_doc)  # start with original document, then apply edits
        edit_exceptions = []
        for page_no, (strikethroughs, inserts) in sorted(list(page_edits.items()), key=lambda x: x[0]):
            page = specs_doc.load_page(page_no)
            try:
                edited = multiple_split_edits(page, strikethroughs, inserts)
                edited_doc.delete_page(page_no)
                edited_doc.insert_pdf(edited, from_page=0, to_page=0, start_at=page_no)
            except Exception as e:
                print(f'Error processing page {page_no}: {e}')
                edit_exceptions.append((page_no, e))
    edit_apply_seconds = time.time() - edit_apply_start
    st.write(f'✅ Edits applied in {edit_apply_seconds//60} minutes {edit_apply_seconds%60:.1f} seconds')
    return edited_doc, edits_exceptions, edit_exceptions

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
    st.write(f'Finished in {st.session_state["merge_time"]//60} minutes {st.session_state["merge_time"]%60:.1f} seconds')
    st.write('It is recommended to review the edited specification to ensure all changes were applied as expected.')

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
                for section_no, e in edit_errs:
                    st.markdown(f'Error in section {section_no}:')
                    st.warning(f'{e}')
                    traceback.print_exception(e)
            if insert_errs:
                st.markdown("**PDF Insert Errors**")
                for page_no, e in insert_errs:
                    st.markdown(f'Error on page {page_no}:')
                    st.warning(f'{e}')
                    traceback.print_exception(e)
    else:
        st.info("0 errors encountered during the merge process.")

else:
    st.html(
        '<p class="app-description">Upload an SRCS (revisions) PDF to merge into the FP-14 specification document.</p>',
    )

    specs_file_options = [spec_file['name'] for spec_file in st.session_state['config']['specs_files']]
    selected_specs_file_name = st.pills(
        label = 'Select a specs file to edit',
        options = specs_file_options,
        default = 'FP-14' if 'FP-14' in specs_file_options else None,
        selection_mode = 'single'
    )

    uploaded_file = st.file_uploader(
        "Upload SRCS PDF",
        type=["pdf"],
        accept_multiple_files=False,
        help="Select the SRCS revision document you want to merge into FP-14.",
    )

    if st.button(
        ":material/play_arrow: Run SpecMerge",
        type="primary",
        disabled=(uploaded_file is None) or (selected_specs_file_name is None),
        use_container_width=True,
    ):

        with st.status("Merging specifications (may take several minutes)...", expanded=True) as status:
            merge_start = time.time()
            specs_file_path = [spec_file['path'] for spec_file in st.session_state['config']['specs_files'] if
                               spec_file['name'] == selected_specs_file_name][0]
            specs_doc = fitz.open(specs_file_path)
            srcs_doc = fitz.open(stream=uploaded_file.read(), filetype="pdf")

            edited_doc, edits_exceptions, insert_exceptions = asyncio.run(get_edited_doc(specs_doc, srcs_doc))
            st.session_state['edited_doc_bytes'] = edited_doc.tobytes()
            st.session_state['edits_exceptions'] = edits_exceptions
            st.session_state['insert_exceptions'] = insert_exceptions
            st.session_state['merge_time'] = time.time() - merge_start
            status.update(label="Merge complete!", state="complete")
            time.sleep(1)  # ensure status update is seen before rerun
        st.rerun()