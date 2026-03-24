import fitz
from markdown import markdown


def split_insert(page: fitz.Page, split_y: int|float, insert_md: str, box_x_margin = 50, box_y_margin = 20, insert_margin = 10) -> fitz.Document:
    insert_html = markdown(insert_md)

    split_height = 300
    new_doc = fitz.Document()
    new_page = new_doc.new_page(width = page.rect.width, height = page.rect.height+split_height)
    text_rect = fitz.Rect(box_x_margin+insert_margin, split_y+box_y_margin+insert_margin, page.rect.width-box_x_margin-insert_margin, split_y+split_height-box_y_margin-insert_margin)
    underflow = new_page.insert_htmlbox(text_rect, insert_html)[0]
    split_height-=underflow

    new_doc = fitz.Document()
    new_page = new_doc.new_page(width = page.rect.width, height = page.rect.height+split_height)

    zoom_mat = fitz.Matrix(2, 2)
    top_pixmap = page.get_pixmap(clip = fitz.Rect(0, 0, page.rect.width, split_y), matrix = zoom_mat)
    top_pix = top_pixmap.tobytes('jpeg', jpg_quality = 85)
    bottom_pixmap = page.get_pixmap(clip = fitz.Rect(0, split_y, page.rect.width, page.rect.height), matrix = zoom_mat)
    bottom_pix = bottom_pixmap.tobytes('jpeg', jpg_quality = 85)

    new_page.insert_image(fitz.Rect(0, 0, page.rect.width, split_y), stream = top_pix)
    new_page.insert_image(fitz.Rect(0, split_y+split_height, page.rect.width, page.rect.height+split_height), stream = bottom_pix)
    words = page.get_text('words')
    for word in words:
        x0, y0, x1, y1, word_text, _, _, _ = word
        if y1 <= split_y:
            new_page.insert_text((x0, y1-2), word_text, fontsize=8, stroke_opacity = 0, fill_opacity = 0)
        elif y0 >= split_y:
            new_page.insert_text((x0, y1+split_height-2), word_text, fontsize=8, stroke_opacity = 0, fill_opacity = 0)
        else:
            top_overlap = (split_y-y0)/(y1-y0)
            if top_overlap>=.5:
                new_page.insert_text((x0, y1-2), word_text, fontsize=8, stroke_opacity = 0, fill_opacity = 0)
            else:
                new_page.insert_text((x0, y1+split_height-2), word_text, fontsize=8, stroke_opacity = 0, fill_opacity = 0)

    rect_annot_rect = fitz.Rect(box_x_margin, split_y+box_y_margin, page.rect.width-box_x_margin, split_y+split_height-box_y_margin)
    new_page.add_rect_annot(rect_annot_rect)
    text_rect = fitz.Rect(box_x_margin+insert_margin, split_y+box_y_margin+insert_margin, page.rect.width-box_x_margin-insert_margin, split_y+split_height-box_y_margin)
    new_page.insert_htmlbox(text_rect, insert_html)
    return new_doc

def get_overlap_frac(word_rect: fitz.Rect, bbox: fitz.Rect) -> float:
    intersect_x0 = max(word_rect.x0, bbox.x0)
    intersect_y0 = max(word_rect.y0, bbox.y0)
    intersect_x1 = min(word_rect.x1, bbox.x1)
    intersect_y1 = min(word_rect.y1, bbox.y1)
    if intersect_x0 >= intersect_x1 or intersect_y0 >= intersect_y1:
        return 0.0
    intersect_area = (intersect_y1-intersect_y0) * (intersect_x1-intersect_x0)
    return intersect_area/word_rect.get_area()

def get_text_bbox(page: fitz.Page, bbox: fitz.Rect, overlap_tol = 0.5):
    words = page.get_text('words')
    text_x0 = float('inf')
    text_x1 = float('-inf')
    text_y0 = float('inf')
    text_y1 = float('-inf')
    for word in words:
        x0, y0, x1, y1, word_text, _, _, _ = word
        overlap_frac = get_overlap_frac(fitz.Rect(x0, y0, x1, y1), bbox)
        if overlap_frac>=overlap_tol:
            text_x0 = min(text_x0, x0)
            text_x1 = max(text_x1, x1)
            text_y0 = min(text_y0, y0)
            text_y1 = max(text_y1, y1)
    if text_x0 == float('inf'):
        return None
    return text_x0, text_y0, text_x1, text_y1

def multiple_split_insert(
        page: fitz.Page,
        inserts: list[tuple[float, str]],
        strikethroughs: list[tuple[float, float]],
        box_x_margin = 50,
        box_y_margin = 20,
        insert_margin = 10
) -> fitz.Document:
    font_size_css = """
body { font-size: 11px; font-family: helvetica; line-height: 1.4; overflow-wrap: break-word; word-break: break-word; }
pre, code { white-space: pre-wrap; overflow-wrap: break-word; }
h1 { font-size: 2em; }
h2 { font-size: 1.5em; }
h3 { font-size: 1.2em; }
h4 { font-size: 1.1em; }
h5 { font-size: 1em; }
h6 { font-size: 0.9em; }
p, li, td, th, dd, dt, blockquote, pre, code, a, abbr, sup { font-size: 1em; }
ul, ol { margin-left: 1.5em; font-size: 1em; }
table { border-collapse: collapse; font-size: 1em; }
strong { font-weight: bold; }
em { font-style: italic; }
hr { border: none; border-top: 1px solid #ccc; }
"""

    inserts = sorted(inserts, key = lambda x: x[0])
    split_heights = []
    for split_y, insert_md in sorted(inserts, key = lambda x: x[0]):
        insert_html = markdown(insert_md, extensions = ['extra'])
        # print(insert_html)
        # break

        split_height = 300
        scale = .9
        while scale<1.:
            new_doc = fitz.Document()
            new_page = new_doc.new_page(width = page.rect.width, height = page.rect.height+split_height)
            text_rect = fitz.Rect(box_x_margin+insert_margin, split_y+box_y_margin+insert_margin, page.rect.width-box_x_margin-insert_margin, split_y+split_height-box_y_margin-insert_margin)


            # print(text_rect) # todo remove

            spare_height, scale = new_page.insert_htmlbox(text_rect, insert_html, css = font_size_css)
            if scale<1.:
                # print('scaling to', scale, 'from', split_height)
                split_height = ((split_height-(2*(insert_margin+box_y_margin)))/scale) + 2*(insert_margin+box_y_margin)
                # print('adjusted split height:', split_height)
            else:
                split_height -= spare_height

        # #todo remove
        # text_rect = fitz.Rect(box_x_margin+insert_margin, split_y+box_y_margin+insert_margin, page.rect.width-box_x_margin-insert_margin, split_y+split_height-box_y_margin-insert_margin)
        # spare_height, scale = new_page.insert_htmlbox(text_rect, insert_html, css = font_size_css, scale_low = .9)
        # if spare_height == -1:
        #     text_rect = fitz.Rect(box_x_margin+insert_margin, split_y+box_y_margin+insert_margin, page.rect.width-box_x_margin-insert_margin, split_y+split_height-box_y_margin)
        #     spare_height, scale = new_page.insert_htmlbox(text_rect, insert_html, css = font_size_css)
        #     print(f'Warning: content overflowed the allocated split height of {split_height} pts even after adjustment, with scale {scale}.')


        split_heights.append(split_height)
    out_doc = fitz.Document()
    out_page = out_doc.new_page(width=page.rect.width, height=page.rect.height+sum(split_heights))

    strikethroughs = sorted(strikethroughs)

    zoom_mat = fitz.Matrix(2, 2)
    words = page.get_text('words')
    pix_start_y = 0
    y_offset = 0
    insert_ind = 0
    strikethrough_ind = 0
    while insert_ind<len(inserts) or strikethrough_ind<len(strikethroughs):
        strikethrough_turn = False
        if strikethrough_ind < len(strikethroughs):
            strikethrough_y0, strikethrough_y1 = strikethroughs[strikethrough_ind]
            strikethrough_y = (strikethrough_y0 + strikethrough_y1) / 2
            if insert_ind == len(inserts) or strikethrough_y<inserts[insert_ind][0]:
                strikethrough_turn = True
                text_x0, text_y0, text_x1, text_y1 = get_text_bbox(page, fitz.Rect(0, strikethrough_y0, page.rect.width, strikethrough_y1))
                out_page.add_line_annot((text_x0-2, strikethrough_y+y_offset), (text_x1+2, strikethrough_y+y_offset))
                strikethrough_ind+=1

        if not strikethrough_turn:
            split_height = split_heights[insert_ind]
            split_y, insert_md = inserts[insert_ind]
            insert_html = markdown(insert_md, extensions = ['extra'])

            if split_y-pix_start_y>1.:
                top_pixmap = page.get_pixmap(clip = fitz.Rect(0, pix_start_y, page.rect.width, split_y), matrix = zoom_mat)
                top_pix = top_pixmap.tobytes('jpeg', jpg_quality = 85)

                out_page.insert_image(fitz.Rect(0, pix_start_y+y_offset, page.rect.width, split_y+y_offset), stream=top_pix)

                for word in words:
                    x0, y0, x1, y1, word_text, _, _, _ = word
                    top_overlap = max(0, y1 - max(y0, pix_start_y)) / (y1 - y0)
                    if top_overlap<.5:
                        continue
                    bottom_overlap = max(0, min(y1, split_y) - y0) / (y1 - y0)
                    if bottom_overlap>=.5:
                        out_page.insert_text((x0, y1+y_offset - 2), word_text, fontsize=8, stroke_opacity=0, fill_opacity=0)

            rect_annot_rect = fitz.Rect(box_x_margin, split_y + y_offset + box_y_margin, page.rect.width - box_x_margin,
                                        split_y + y_offset + split_height - box_y_margin)
            out_page.add_rect_annot(rect_annot_rect)
            text_rect = fitz.Rect(box_x_margin + insert_margin, split_y + y_offset + box_y_margin + insert_margin,
                                  page.rect.width - box_x_margin - insert_margin, split_y + y_offset + split_height - box_y_margin)
            spare_height, scale = out_page.insert_htmlbox(text_rect, insert_html, css=font_size_css, scale_low=.9)
            if spare_height == -1:
                print('Warning: content overflowed the allocated split height')
            pix_start_y = split_y
            y_offset+=split_height
            insert_ind += 1

    if page.rect.height - pix_start_y > 1.:
        bottom_pixmap = page.get_pixmap(clip = fitz.Rect(0, pix_start_y, page.rect.width, page.rect.height), matrix = zoom_mat)
        bottom_pix = bottom_pixmap.tobytes('jpeg', jpg_quality = 85)
        out_page.insert_image(fitz.Rect(0, pix_start_y+y_offset, page.rect.width, page.rect.height+y_offset), stream = bottom_pix)
        for word in words:
            x0, y0, x1, y1, word_text, _, _, _ = word
            overlap = max(0, y1 - max(y0, pix_start_y)) / (y1 - y0)
            if overlap >= .5:
                out_page.insert_text((x0, y1 + y_offset - 2), word_text, fontsize=8, stroke_opacity=0, fill_opacity=0)

    return out_doc

def _avg_char_width(word_tuples):
    total_width = 0
    total_chars = 0
    for w in word_tuples:
        x0, _, x1, _, text, _, _, _ = w
        n = len(text)
        if n > 0:
            total_width += (x1 - x0)
            total_chars += n
    return total_width / total_chars if total_chars > 0 else 0

def get_page_lines(page: fitz.Page, line_tolerance: float = 0.8) -> list[list[float, float, str]]:
    words = page.get_text('words')
    sorted_words = sorted(words, key=lambda w: w[1]) # sort by y0 (top of word)
    lines = []
    curr_line_words = []

    # page-wide average char width as fallback for leading indent
    global_avg = _avg_char_width(sorted_words) or 1.0

    for word in sorted_words:
        x0, y0, x1, y1, word_text, _, _, _ = word


        if len(word_text.strip()) == 0:
            continue

        if len(curr_line_words) == 0:
            curr_line_words.append(word)
            lines.append([y0, y1])
            continue

        overlap = max(0, min(y1, curr_line_words[-1][3]) - max(y0, curr_line_words[-1][1])) / (y1 - y0)

        if overlap > line_tolerance:
            curr_line_words.append(word)
        else:
            average_char_w = _avg_char_width(curr_line_words) or global_avg
            curr_line_words = sorted(curr_line_words, key=lambda w: w[0]) # sort by x0 (left of word)
            leading_spaces = max(0, round(curr_line_words[0][0] / average_char_w))
            line_text = ' '*leading_spaces
            line_text+=curr_line_words[0][4]
            prev_x1 = curr_line_words[0][2]
            for w in curr_line_words[1:]:
                gap = w[0]-prev_x1
                num_spaces = max(1, round(gap / average_char_w)) if average_char_w > 0 else 1
                line_text += (' ' * num_spaces) + w[4]
                prev_x1 = w[2]
            lines[-1].append(line_text)
            curr_line_words = [word]
            lines.append([y0, y1])
    average_char_w = _avg_char_width(curr_line_words) or global_avg
    curr_line_words = sorted(curr_line_words, key=lambda w: w[0]) # sort by x0 (left of word)
    leading_spaces = max(0, round(curr_line_words[0][0] / average_char_w)) if average_char_w > 0 else 1
    line_text = ' '*leading_spaces
    line_text+=curr_line_words[0][4]
    prev_x1 = curr_line_words[0][2]
    for w in curr_line_words[1:]:
        gap = w[0]-prev_x1
        num_spaces = max(1, round(gap / average_char_w)) if average_char_w > 0 else 1

        line_text += (' ' * num_spaces) + w[4]
        prev_x1 = w[2]
    lines[-1].append(line_text)

    return lines