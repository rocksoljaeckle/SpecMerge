from pydantic import BaseModel
import fitz
import openai
from openai import AsyncOpenAI
import asyncio
import bisect

from pdf_utils import get_page_lines, split_insert

class PageEdit(BaseModel):
    above_line_hex: str
    insert_text: str

class StrikeThroughEdit(BaseModel):
    line_hex: str

class EditsList(BaseModel):
    edits: list[PageEdit | StrikeThroughEdit]
    explanation: str # todo remove

async def get_section_edits(
        section_no: str,
        specs_sections_pages: dict, srcs_sections_pages: dict,
        specs_doc: fitz.Document, srcs_doc: fitz.Document,
        edit_prompt: str, openai_client: AsyncOpenAI,
        sem: asyncio.Semaphore,
        model = 'gpt-5.4'
) -> dict[int, tuple[list[tuple[float, str]], list[tuple[float, float]]]]:
    if section_no not in srcs_sections_pages:
        raise ValueError(f'Section {section_no} not found in SCRS')

    if section_no not in specs_sections_pages:
        section_no_float = float(section_no)
        specs_sections_keys = list(specs_sections_pages.keys())
        specs_sections_floats = [float(s) for s in specs_sections_keys]
        insert_pos = bisect.bisect_left(specs_sections_floats, section_no_float)
        specs_page_nos = []
        if insert_pos > 0:
            specs_page_nos.extend(specs_sections_pages[specs_sections_keys[insert_pos - 1]])
        if insert_pos < len(specs_sections_floats)-1:
            specs_page_nos.extend(specs_sections_pages[specs_sections_keys[insert_pos]])
    else:
        specs_page_nos = sorted(specs_sections_pages[section_no])
    specs_text = ''
    specs_line_ind = 0
    specs_lines_dict = dict()# map line hex to (page_no, y1)
    for spec_pno in specs_page_nos:
        spec_page = specs_doc.load_page(spec_pno)
        spec_lines = get_page_lines(spec_page)
        for y0, y1, line in spec_lines:
            line_hex = hex(specs_line_ind)
            specs_text += f'[{line_hex}] {line}\n'
            specs_lines_dict[line_hex] = (spec_pno, y0, y1)
            specs_line_ind += 1

    srcs_text = '\n\n'.join([srcs_doc.load_page(pno).get_text(sort=True) for pno in sorted(srcs_sections_pages[section_no])])

    messages = [
        {
            'role': 'system',
            'content': edit_prompt
        },
        {
            'role': 'user',
            'content': f'Here is the text content of the relevant section from the specs, with line hex addresses: {specs_text}'
        },
        {
            'role': 'user',
            'content': f'Here is the text content of the relevant section from the SCRS: {srcs_text}'
        },
        {
            'role': 'user',
            'content': f'Please determine edits for the section number {section_no}. If the section is not present in the provided text, return an empty edits list. Do not edit text that is not part of the section.'
        }
    ]

    async with sem:
        response = await openai_client.responses.parse(
            input = messages,
            model = model,
            text_format = EditsList
        )
    # print('explanation:', response.output_parsed.explanation) # todo remove
    edits = response.output_parsed.edits

    # Map edits back to page numbers and y-coordinates
    page_edits = dict()
    for edit in edits:
        if isinstance(edit, PageEdit):
            line_hex = edit.above_line_hex
            insert_md = edit.insert_text
            if line_hex not in specs_lines_dict:
                raise ValueError(f'Line hex {line_hex} not found in specs lines dict')
            page_no, y0, y1 = specs_lines_dict[line_hex]
            if page_no not in page_edits:
                page_edits[page_no] = ([],[])
            page_edits[page_no][0].append((y1, insert_md))
        elif isinstance(edit, StrikeThroughEdit):
            line_hex = edit.line_hex
            if line_hex not in specs_lines_dict:
                raise ValueError(f'Line hex {line_hex} not found in specs lines dict')
            page_no, y0,  y1 = specs_lines_dict[line_hex]
            if page_no not in page_edits:
                page_edits[page_no] = ([],[])
            page_edits[page_no][1].append((y0, y1))
    return page_edits

def get_edited_page(page: fitz.Page, edit_prompt: str, client: openai.OpenAI):
    page_lines = get_page_lines(page)
    page_text = ''
    lines_map = dict()
    for line_ind, line in enumerate(page_lines):
        line_hex = hex(line_ind)
        lines_map[line_hex] = line
        page_text+=f'[{line_hex}] {line[2]}\n'

    messages = [
        {
            'role': 'system',
            'content': edit_prompt,
        },
        {
            'role':'user',
            'content': f'Here is the text of the page:\n{page_text}'
        }
    ]

    response = client.responses.parse(
        model = 'gpt-5.4',
        input = messages,
        text_format = PageEdit
    )
    page_edit = response.output_parsed
    # def split_insert(page: fitz.Page, split_y, insert_text, box_x_margin = 50, box_y_margin = 20, insert_margin = 10):
    split_y = lines_map[page_edit.above_line_hex][1] # y1 of line above which to insert text
    return split_insert(page, split_y, page_edit.insert_text)
