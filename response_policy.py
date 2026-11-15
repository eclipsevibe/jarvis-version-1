"""Explicit response-time estimates for the local assistant."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ResponseBudget:
    level: str
    expected_seconds: int
    output_tokens: int

    @property
    def handoff_seconds(self):
        return self.expected_seconds * 3


def estimate(prompt):
    text = prompt.lower()
    if re.search(r'\b(open|show|watch|play|find|search)\b',text) and re.search(r'\b(youtube|video|webpage|website)\b|\bin (?:chrome|firefox|browser)\b',text):
        return ResponseBudget('web action',15,128)
    if re.search(r'\bplay\s+\S+', text):
        return ResponseBudget('music', 15, 128)
    if re.search(r'\b(debug|implement|program|write code|prove|research|analyse code|analyze code)\b', text):
        return ResponseBudget('complex', 30, 768)
    if re.search(r'\b(detailed|in depth|in-depth|step.by.step|compare|comparison|comprehensive|essay|report|plan)\b', text) or len(text.split()) > 65:
        return ResponseBudget('detailed', 20, 512)
    if re.search(r'\b(short|brief|briefly|one sentence|two sentences|1 sentence|2 sentences)\b', text) or len(text.split()) <= 12:
        return ResponseBudget('short', 5, 128)
    return ResponseBudget('ordinary', 10, 256)
