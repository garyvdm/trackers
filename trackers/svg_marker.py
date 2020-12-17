import logging
from functools import lru_cache
from subprocess import DEVNULL, PIPE, run

from datauri import DataURI

log = logging.getLogger(__name__)

directions = {
    'n': lambda center, right: (
        1, 9,  # translate
        0,  # arrow_width
        center, 0,  # origin
        (
            f'M{center},-8'
            'l-8,8'
            'l16,0'
            'z'
        ),
    ),
    'ne': lambda center, right: (
        1, 9,  # translate
        8,  # arrow_width
        right, 0,  # origin
        (
            f'M{right},-8'
            'l-24,8'
            'l16,8'
            'z'
        ),
    ),
    'nw': lambda center, right: (
        9, 9,  # translate
        8,  # arrow_width
        0, 0,  # origin
        (
            'M-8,-8'
            'l24,8'
            'l-16,8'
            'z'
        ),
    ),
    's': lambda center, right: (
        1, 1,  # translate
        0,  # arrow_width
        center, 34,  # origin
        (
            f'M{center},34'
            'l-8,-8'
            'l16,0'
            'z'
        ),
    ),
    'se': lambda center, right: (
        1, 1,  # translate
        8,  # arrow_width
        right, 34,  # origin
        (
            f'M{right},34'
            'l-24,-8'
            'l16,-8'
            'z'
        ),
    ),
    'sw': lambda center, right: (
        9, 1,  # translate
        8,  # arrow_width
        0, 34,  # origin
        (
            'M-8,34'
            'l24,-8'
            'l-16,-8'
            'z'
        ),
    ),
}


@lru_cache()
def _text_width(text, style):
    # TODO: speed this up by doing 1 call for all texts.
    try:
        log.debug(f'text_width {text}')
        text_query_file = (
            '<?xml version="1.0" ?>'
            '<svg  xmlns="http://www.w3.org/2000/svg" width="300px" height="36px" viewBox="0 0 300 36" version="1.1" >'
            f'<text id="label" style="{style}">{text}</text>'
            '</svg>'
        )  # NOQA E131
        text_width_text = run(['inkscape', '--pipe', '--query-width', '--query-id', 'label'],
                              input=text_query_file, encoding='utf8', stdout=PIPE, stderr=DEVNULL).stdout
        return float(text_width_text)
    except Exception:
        logging.exception('Error getting marker text width:')
        return 100


def svg_marker(text, color='white', background_color='black', direction='se'):
    text_style = f'font-size:13px;font-family:Roboto,Arial,Helvetica,sans-serif;fill:{color};'

    text_width = int(round(_text_width(text, text_style)))
    rect_width = text_width + 24
    rect_height = 26

    translate_x, translate_y, arrow_width, origin_x, origin_y, arrow, = directions[direction](rect_width / 2, rect_width + 8)

    img_width = rect_width + 2 + arrow_width
    img_height = rect_height + 2 + 8

    view_box = f'-{translate_x} -{translate_y} {img_width} {img_height}'

    image = (
        f'<?xml version="1.0" ?>'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" width="{img_width}px" height="{img_height}px" >'
            f'<rect width="{rect_width}" height="{rect_height}" rx="3" ry="3" fill="{background_color}"></rect>'  # NOQA E128
            f'<path d="{arrow}" fill="{background_color}"></path>'
            f'<text y="18" x="12" style="{text_style}">{text}</text>'
        f'</svg>'
    )   # NOQA E131

    return {
        'icon': {
            'url': str(DataURI.make('image/svg+xml', charset='utf8', base64=True, data=image)),
            'anchor': {'x': origin_x + 1, 'y': origin_y + 1},
        },
        'shape': {
            'type': 'rect',
            'coords': (translate_x, translate_y, rect_width + translate_x, rect_height + translate_y),
        }
    }


if __name__ == '__main__':
    import pprint
    import tempfile

    marker = svg_marker('Marker Label j', 'white', 'red', 'nw')

    pprint.pprint(marker)
    print(len(marker['icon']['url']))

    image = DataURI(marker['icon']['url']).data
    print(image.decode())

    path = tempfile.mktemp(prefix='.svg')

    with open(path, 'wb') as f:
        f.write(image)
    run(['firefox', path])
    # run(['xdg-open', path])
