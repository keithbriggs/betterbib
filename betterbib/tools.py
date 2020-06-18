import codecs
import configparser
import os
import re

import appdirs
import enchant

# for "ulatex" codec
import latexcodec  # noqa
import requests

from .__about__ import __version__
from .errors import UniqueError

_config_dir = appdirs.user_config_dir("betterbib")
if not os.path.exists(_config_dir):
    os.makedirs(_config_dir)
_config_file = os.path.join(_config_dir, "config.ini")


def decode(d):
    """Decode a dictionary with LaTeX strings into a dictionary with unicode strings.
    """
    for entry in d.values():
        for key, value in entry.fields.items():
            if key == "url":
                # The url can contain special LaTeX characters (like %) and that's fine
                continue
            entry.fields[key] = codecs.decode(value, "ulatex")
    return d


def pybtex_to_dict(entry):
    """dict representation of BibTeX entry.
    """
    d = {}
    d["genre"] = entry.type
    for key, persons in entry.persons.items():
        d[key.lower()] = [
            {
                "first": p.first_names,
                "middle": p.middle_names,
                "prelast": p.prelast_names,
                "last": p.last_names,
                "lineage": p.lineage_names,
            }
            for p in persons
        ]
    for field, value in entry.fields.items():
        d[field.lower()] = value
    return d


def translate_month(key):
    """The month value can take weird forms. Sometimes, it's given as an int, sometimes
    as a string representing an int, and sometimes the name of the month is spelled out.
    Try to handle most of this here.
    """
    months = [
        "jan",
        "feb",
        "mar",
        "apr",
        "may",
        "jun",
        "jul",
        "aug",
        "sep",
        "oct",
        "nov",
        "dec",
    ]

    # Sometimes, the key is just a month
    try:
        return months[int(key) - 1]
    except (TypeError, ValueError):
        # TypeError: unsupported operand type(s) for -: 'str' and 'int'
        pass

    # Split for entries like "March-April"
    strings = []
    for k in key.split("-"):
        month = k[:3].lower()

        # Month values like '????' appear -- skip them
        if month in months:
            strings.append(month)
        else:
            print("Unknown month value '{}'. Skipping.".format(key))
            return None

    return ' # "-" # '.join(strings)


def create_dict():
    d = enchant.DictWithPWL("en_US")

    # read extra names from config file
    config = configparser.ConfigParser()
    config.read(_config_file)
    try:
        extra_names = config.get("DICTIONARY", "add").split(",")
    except (configparser.NoSectionError, configparser.NoOptionError):
        # No section: 'DICTIONARY', No option 'add' in section: 'DICTIONARY'
        extra_names = []

    for name in extra_names:
        name = name.strip()
        d.add(name)
        d.add(name + "'s")
        if name[-1] == "s":
            d.add(name + "'")

    try:
        blacklist = config.get("DICTIONARY", "remove").split(",")
    except (configparser.NoSectionError, configparser.NoOptionError):
        blacklist = []
    for word in blacklist:
        d.remove(word.strip())

    return d


def _translate_word(word, d):
    # Check if the word needs to be protected by curly braces to prevent
    # recapitalization.
    if not word:
        needs_protection = False
    elif word.count("{") != word.count("}"):
        needs_protection = False
    elif word[0] == "{" and word[-1] == "}":
        needs_protection = False
    elif any([char.isupper() for char in word[1:]]):
        needs_protection = True
    else:
        needs_protection = (
            any([char.isupper() for char in word])
            and d.check(word)
            and not d.check(word.lower())
        )

    if needs_protection:
        return "{" + word + "}"
    return word


def _translate_title(val, dictionary=create_dict()):
    """The capitalization of BibTeX entries is handled by the style, so names (Newton)
    or abbreviations (GMRES) may not be capitalized. This is unless they are wrapped in
    curly braces.
    This function takes a raw title string as input and {}-protects those parts whose
    capitalization should not change.
    """
    # If the title is completely capitalized, it's probably by mistake.
    if val == val.upper():
        val = val.title()

    words = val.split()
    # Handle colons as in
    # ```
    # Algorithm 694: {A} collection...
    # ```
    for k in range(len(words)):
        if k > 0 and words[k - 1][-1] == ":" and words[k][0] != "{":
            words[k] = "{" + words[k].capitalize() + "}"

    words = [
        "-".join([_translate_word(w, dictionary) for w in word.split("-")])
        for word in words
    ]

    return " ".join(words)


def pybtex_to_bibtex_string(
    entry,
    bibtex_key,
    brace_delimeters=True,
    tab_indent=False,
    dictionary=create_dict(),
    sort=False,
):
    """String representation of BibTeX entry.
    """
    indent = "\t" if tab_indent else " "
    out = "@{}{{{},\n{}".format(entry.type, bibtex_key, indent)
    content = []

    left, right = ["{", "}"] if brace_delimeters else ['"', '"']

    for key, persons in entry.persons.items():
        persons_str = " and ".join([_get_person_str(p) for p in persons])
        content.append("{} = {}{}{}".format(key.lower(), left, persons_str, right))

    keys = entry.fields.keys()
    if sort:
        keys = sorted(keys)

    for key in keys:
        value = entry.fields[key]

        # handle things that latexcodec can't handle yet
        repl = {
            # https://github.com/mcmtroffaes/latexcodec/issues/74:
            # "\u2217": "\\ast",
            # https://github.com/mcmtroffaes/latexcodec/issues/83:
            "\ufffd": "?",
            # https://github.com/mcmtroffaes/latexcodec/issues/84:
            "\u02bb": "`",
            "\u02bc": "`",
            "«": "{\guillemotleft}",
            "»": "{\guillemotlright}"
        }
        for k, val in repl.items():
            try:
                value = value.replace(k, val)
            except AttributeError:
                pass

        try:
            if key not in ["url", "doi"]:
                value = codecs.encode(value, "ulatex")
        except TypeError:
            # expected unicode for encode input, but got int instead
            pass

        # Always make keys lowercase
        key = key.lower()

        if key == "month":
            month_string = translate_month(value)
            if month_string:
                content.append("{} = {}".format(key, month_string))
        elif key == "title":
            content.append(
                "{} = {}{}{}".format(
                    key, left, _translate_title(value, dictionary), right
                )
            )
        else:
            if value is not None:
                content.append("{} = {}{}{}".format(key, left, value, right))

    # Make sure that every line ends with a comma
    out += indent.join([line + ",\n" for line in content])
    out += "}"
    return out


def doi_from_url(url):
    """See if this is a DOI URL and return the DOI.
    """
    m = re.match("https?://(?:dx\\.)?doi\\.org/(.*)", url)
    if m:
        return m.group(1)
    return None


def get_short_doi(doi):
    url = "http://shortdoi.org/" + doi
    r = requests.get(url, params={"format": "json"})
    if not r.ok:
        return None

    data = r.json()
    if "ShortDOI" not in data:
        return None

    return data["ShortDOI"]


def _join_abbreviated_names(lst):
    """Joins a list of name strings like ["J.", "Frank"] such that it adds a space if
    one of the two names is not abbreviated. See
    <https://english.stackexchange.com/a/105529/23644>.
    """
    if len(lst) == 0:
        return ""

    out = lst[0]
    last_ends_in_dot = lst[0][-1] == "."
    for item in lst[1:]:
        ends_in_dot = item[-1] == "."
        if last_ends_in_dot and ends_in_dot:
            out += item
        else:
            out += " " + item
        last_ends_in_dot = ends_in_dot
    return out


def _get_person_str(p):
    out = ", ".join(
        filter(
            None,
            [
                " ".join(p.prelast_names + p.last_names),
                " ".join(p.lineage_names),
                _join_abbreviated_names(p.first_names + p.middle_names),
            ],
        )
    )
    # If the name is completely capitalized, it's probably by mistake.
    if out == out.upper():
        out = out.title()
    return out


def prettyprint_result(res):
    string = []
    if "score" in res:
        string.append(f"score:     {res['score']}")
    if "type" in res:
        string.append(f"type:      {res['type']}")
    if "title" in res:
        string.append(f"title:     {res['title'][0]}")
    if "DOI" in res:
        string.append(f"DOI:       {res['DOI']}")
    if "page" in res:
        string.append(f"page:      {res['page']}")
    if "publisher" in res:
        string.append(f"publisher: {res['publisher']}")
    if "container-title" in res:
        string.append(f"container: {res['container-title'][0]}")
    if "volume" in res:
        string.append(f"volume:    {res['volume']}")
    if "issue" in res:
        string.append(f"issue:     {res['issue']}")
    if "reference-count" in res:
        string.append(f"ref count: {res['reference-count']}")
    if "language" in res:
        string.append(f"language:  {res['language']}")
    return "\n".join(string)


def heuristic_unique_result(results, d):
    # Q: How to we find the correct solution if there's more than one search result?
    # As a heuristic, assume that the top result is the unique answer if its score is at
    # least 1.5 times the score of the the second-best result.
    for score in ["score", "@score"]:
        try:
            if float(results[0][score]) > 1.5 * float(results[1][score]):
                return results[0]
        except KeyError:
            pass

    # If that doesn't work, check if the DOI matches exactly with the input.
    if "doi" in d:
        # sometimes, the doi field contains a doi url
        doi = doi_from_url(d["doi"])
        if not doi:
            doi = d["doi"]
        for result in results:
            for doi_key in "doi", "DOI":
                try:
                    if result[doi_key].lower() == doi.lower():
                        return result
                except KeyError:
                    pass

    # If that doesn't work, check if the title appears in the input.
    if "title" in d:
        for result in results:
            if (
                "title" in result
                and result["title"]
                and result["title"][0].lower() in d["title"].lower()
            ):
                return result

    # If that doesn't work, check if the page range matches exactly with the input.
    if "pages" in d:
        for result in results:
            if "page" in result and result["page"] == d["pages"]:
                return result

    # If that doesn't work, check if the second entry is a JSTOR copy of the original
    # article -- yes, that happens --, and take the first one.
    if (
        "publisher" in results[1]
        and results[1]["publisher"] == "JSTOR"
        and "title" in results[0]
        and "title" in results[1]
        and results[0]["title"][0].lower() == results[1]["title"][0].lower()
    ):
        return results[0]

    raise UniqueError(
        "Could not find a positively unique match. "
        "Got\n\n{}\n\nand\n\n{}\n".format(
            prettyprint_result(results[0]), prettyprint_result(results[1])
        )
    )


def write(od, file_handle, delimeter_type, tab_indent):
    # Create the dictionary only once
    dictionary = create_dict()

    # Write header to the output file.
    segments = [
        "%comment{{This file was created with betterbib v{}.}}\n".format(__version__)
    ]

    brace_delimeters = delimeter_type == "braces"

    # Add segments for each bibtex entry in order
    segments.extend(
        [
            pybtex_to_bibtex_string(
                d,
                bib_id,
                brace_delimeters=brace_delimeters,
                tab_indent=tab_indent,
                dictionary=dictionary,
            )
            for bib_id, d in od.items()
        ]
    )

    # Write all segments to the file at once
    file_handle.write("\n\n".join(segments) + "\n")


def update(entry1, entry2):
    """Create a merged BibTeX entry with the data from entry2 taking precedence.
    """
    out = entry1
    if entry2 is not None:
        if entry2.type:
            out.type = entry2.type

        if entry2.persons:
            for key, value in entry2.persons.items():
                if value:
                    out.persons[key] = value

        for key, value in entry2.fields.items():
            if value:
                out.fields[key] = value

    return out


def filter_fields(data, excludes=None):
    excludes = excludes or []
    for entry in data.entries.values():
        if entry.fields:
            entry.fields = {k: v for k, v in entry.fields.items() if k not in excludes}
    return data
