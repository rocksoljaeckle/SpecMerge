from pydantic import BaseModel
import fitz
import openai
from openai import AsyncOpenAI
import asyncio
import bisect
from fuzzysearch import find_near_matches
import re

from pdf_utils import (
    get_page_lines,
    get_text_bbox,
    EditsList,
    LLMStrikethroughEdit,
    LLMInsertEdit,
    StrikethroughEdit,
    InsertEdit,
)

def llm_strikethrough_to_edit(
        llm_strikethrough_edit: LLMStrikethroughEdit,
        specs_lines_dict: dict[str, tuple[int, float, float, list]],
        specs_doc: fitz.Document,
) -> tuple[int, float, float]:
    line_hex = llm_strikethrough_edit.line_hex
    if line_hex not in specs_lines_dict:
        raise ValueError(f'Line hex {line_hex} not found in specs lines dict')
    page_no, y0, y1, lines_words = specs_lines_dict[line_hex]
    page = specs_doc.load_page(page_no)
    if llm_strikethrough_edit.substring_text is None:
        text_x0, text_y0, text_x1, text_y1 = get_text_bbox(
            page,
            fitz.Rect(0, y0, page.rect.width, y1),
        )
        return StrikethroughEdit(
            y = (y0+y1)/2,
            x0 = text_x0-2,
            x1 = text_x1+2,
        )
    else:
        substring_text = llm_strikethrough_edit.substring_text
        substring_query = re.sub(r'\s+', ' ', llm_strikethrough_edit.substring_text).strip()
        lines_words = sorted(lines_words, key=lambda w: w[0])
        line_str = ' '.join([w[4] for w in lines_words])
        substring_matches = find_near_matches(substring_query, line_str, max_l_dist=3)
        if len(substring_matches) == 0:
            raise ValueError(f'No match found for substring "{substring_text}" in line "{line_str}"')
        best_match = min(substring_matches, key=lambda m: m.dist)


        strikethrough_x0 = None
        strikethrough_x1 = None
        char_ind = 0
        for word in lines_words:
            word_x0, word_y0, word_x1, word_y1, word_text, _, _, _ = word
            char_ind += len(word_text)
            if strikethrough_x0 is None and char_ind >= best_match.start:
                 strikethrough_x0 = word_x0
            if char_ind >= best_match.end:
                strikethrough_x1 = word_x1
                break
            char_ind+=1 # for the space between words
        if strikethrough_x0 is None:
            raise ValueError(f'Could not determine x0 for substring "{substring_text}" in line "{line_str}"')
        if strikethrough_x1 is None:
            strikethrough_x1 = lines_words[-1][2]
        return StrikethroughEdit(
            y = (y0+y1)/2,
            x0 = strikethrough_x0-2,
            x1 = strikethrough_x1+2,
        )

def llm_insert_to_edit(
        llm_insert_edit: LLMInsertEdit,
        specs_lines_dict: dict[str, tuple[int, float, float, list]],
) -> InsertEdit:
    line_hex = llm_insert_edit.above_line_hex
    if line_hex not in specs_lines_dict:
        raise ValueError(f'Line hex {line_hex} not found in specs lines dict')
    page_no, y0, y1, lines_words = specs_lines_dict[line_hex]
    return InsertEdit(
        y = y1,
        insert_md = llm_insert_edit.insert_md
    )


async def get_section_edits(
        section_no: str,
        specs_sections_pages: dict, srcs_sections_pages: dict,
        specs_doc: fitz.Document, srcs_doc: fitz.Document,
        edit_prompt: str, openai_client: AsyncOpenAI,
        sem: asyncio.Semaphore,
        n_retries: int = 3,
        model = 'gpt-5.4'
) -> dict[int, tuple[list[StrikethroughEdit], list[InsertEdit]]]:
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
    specs_page_nos = sorted(set(specs_page_nos))
    specs_text = ''
    specs_line_ind = 0
    specs_lines_dict = dict()# map line hex to (page_no, y0, y1)
    for spec_pno in specs_page_nos:
        spec_page = specs_doc.load_page(spec_pno)
        spec_lines = get_page_lines(spec_page)
        for y0, y1, line, line_words in spec_lines:
            line_hex = hex(specs_line_ind)
            specs_text += f'[{line_hex}] {line}\n'
            specs_lines_dict[line_hex] = (spec_pno, y0, y1, line_words)
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

    for i in range(n_retries):
        try:
            async with sem:
                response = await openai_client.responses.parse(
                    input = messages,
                    model = model,
                    text_format = EditsList
                )
            # print('explanation:', response.output_parsed.explanation) # todo remove
            llm_strikethrough_edits = response.output_parsed.strikethrough_edits
            llm_insert_edits = response.output_parsed.insert_edits

            # Map edits back to page numbers and y-coordinates
            page_edits = dict()
            for llm_strikethrough_edit in llm_strikethrough_edits:
                line_hex = llm_strikethrough_edit.line_hex
                if line_hex not in specs_lines_dict:
                    raise ValueError(f'Line hex {line_hex} not found in specs lines dict')
                page_no, _,  _, _ = specs_lines_dict[line_hex]
                if page_no not in page_edits:
                    page_edits[page_no] = ([],[])
                strikethrough_edit = llm_strikethrough_to_edit(llm_strikethrough_edit, specs_lines_dict, specs_doc)
                page_edits[page_no][0].append(strikethrough_edit)

            for llm_insert_edit in llm_insert_edits:
                line_hex = llm_insert_edit.above_line_hex
                if line_hex not in specs_lines_dict:
                    raise ValueError(f'Line hex {line_hex} not found in specs lines dict')
                page_no, _, _, _ = specs_lines_dict[line_hex]
                if page_no not in page_edits:
                    page_edits[page_no] = ([],[])
                insert_edit = llm_insert_to_edit(llm_insert_edit, specs_lines_dict)
                page_edits[page_no][1].append(insert_edit)

            return page_edits
        except Exception as e:
            print(f'Error in get_section_edits attempt {i+1}/{n_retries}: {e}')
            if i == n_retries - 1:
                raise e
            await asyncio.sleep(2**i)