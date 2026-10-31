"""Decode a saved web page by the charset it declares, the way a browser does.

fetch_page.py saves the body exactly as the server sent it. Its readers,
`posting_guard.py --html` and `fetch_page.py --text`, decoded that file as
UTF-8 with replacement, so a posting served in EUC-KR, Shift_JIS or
windows-1252 reached the gate and the scorer as replacement characters.

Valid UTF-8 always wins, so a page that decoded cleanly before decodes to the
same string. Only bytes that are not valid UTF-8 fall back to what the page
declares: a UTF-16 byte order mark, then `<meta charset>` or the http-equiv
Content-Type in the first 1024 bytes (the window the HTML prescan reads). A
label is mapped to the codec browsers use for it ("euc-kr" is read as CP949,
"iso-8859-1" as windows-1252). A missing or unknown label keeps UTF-8 with
replacement, and only the labels below are honoured, so a page cannot select
a codec such as UTF-7.
"""

import re

PRESCAN_BYTES = 1024
META_CHARSET = re.compile(rb"<meta\b[^>]*?charset\s*=\s*[\"']?\s*([A-Za-z0-9._:-]+)", re.I)

_CODECS = {
    "cp949": ("euc-kr", "cp949", "windows-949", "ks_c_5601-1987", "ks_c_5601-1989",
              "ksc5601", "ksc_5601", "korean", "iso-ir-149", "csksc56011987"),
    "cp932": ("shift_jis", "shift-jis", "sjis", "x-sjis", "ms_kanji", "ms932",
              "windows-31j", "csshiftjis"),
    "euc_jp": ("euc-jp", "x-euc-jp", "cseucpkdfmtjapanese"),
    "iso2022_jp": ("iso-2022-jp", "csiso2022jp"),
    "gb18030": ("gb18030", "gbk", "gb2312", "x-gbk", "chinese", "csgb2312", "iso-ir-58"),
    "big5hkscs": ("big5", "big5-hkscs", "x-x-big5", "cn-big5", "csbig5"),
    "cp1252": ("windows-1252", "cp1252", "x-cp1252", "iso-8859-1", "iso8859-1",
               "iso_8859-1", "latin1", "l1", "ascii", "us-ascii", "cp819", "ibm819"),
    "cp1250": ("windows-1250", "cp1250", "x-cp1250"),
    "cp1251": ("windows-1251", "cp1251", "x-cp1251"),
    "cp1253": ("windows-1253", "cp1253"),
    "cp1254": ("windows-1254", "cp1254", "iso-8859-9", "latin5"),
    "cp1257": ("windows-1257", "cp1257"),
    "iso8859_2": ("iso-8859-2", "latin2", "l2"),
    "iso8859_5": ("iso-8859-5", "cyrillic"),
    "iso8859_15": ("iso-8859-15", "latin9", "l9"),
    "koi8_r": ("koi8-r", "koi8", "koi"),
    "koi8_u": ("koi8-u",),
}
LABEL_TO_CODEC = {label: codec for codec, labels in _CODECS.items() for label in labels}


def declared_codec(raw: bytes):
    """The codec a page declares through a UTF-16 BOM or a <meta> tag, or None."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    match = META_CHARSET.search(raw[:PRESCAN_BYTES])
    if match is None:
        return None
    return LABEL_TO_CODEC.get(match.group(1).decode("ascii").lower())


def decode_page(raw: bytes) -> str:
    """Text of a saved page: UTF-8 when valid, else its declared charset, else UTF-8 with replacement."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    return raw.decode(declared_codec(raw) or "utf-8", errors="replace")
