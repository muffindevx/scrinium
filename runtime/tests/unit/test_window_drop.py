"""A folder dropped on Scrinium's window reaches the page as a path, never as code."""

import json

from scrinium.cli.window import DROP_EVENT, drop_script, first_local_path


def test_the_first_local_path_is_taken() -> None:
    urls = ["https://example.org/x", "file:///home/ana/mi%20repo", "file:///home/ana/otro"]
    assert first_local_path(urls) == "/home/ana/mi repo"
    # A link dragged from a web page is not a folder on this computer.
    assert first_local_path(["https://example.org/x"]) is None
    assert first_local_path([]) is None


def test_the_path_goes_as_data() -> None:
    path = '/home/ana/a"); alert(1); ("'
    script = drop_script(path)
    assert json.dumps(DROP_EVENT) in script
    assert "{detail: " + json.dumps(path) + "}" in script
