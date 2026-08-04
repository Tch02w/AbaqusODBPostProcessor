"""Compatibility exports for embeddable workbench pages."""

__lazy_modules__ = [
    "abaqus_odb_postprocessor.postprocessor_page",
    "abaqus_odb_postprocessor.result_browser_page",
]

from .postprocessor_page import PostProcessorPage
from .result_browser_page import ResultBrowserPage

__all__ = ["PostProcessorPage", "ResultBrowserPage"]
