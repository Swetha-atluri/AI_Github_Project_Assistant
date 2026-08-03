import json
from typing import Dict, List, Optional
from enum import Enum
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from app import config

# Tech Stack sub-schema
class TechStack(BaseModel):
    languages: List[str] = Field(default_factory=list, description="List of programming languages used.")
    frameworks: List[str] = Field(default_factory=list, description="List of frameworks and libraries used.")
    databases: List[str] = Field(default_factory=list, description="List of databases and data stores used.")
    tools_utilities: List[str] = Field(default_factory=list, description="List of build tools, CI/CD, utilities, or other tools.")

# Severity Enum for Code Quality Issues
class SeverityEnum(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

# Individual Code Quality Review sub-schema
class CodeQualityReview(BaseModel):
    title: str = Field(description="Short title of the code quality issue.")
    description: str = Field(description="Details of the finding and why it is an issue.")
    severity: SeverityEnum = Field(description="Severity of the issue.")
    file_path: Optional[str] = Field(default=None, description="Relative file path where the issue resides, if applicable.")
    code_snippet_before: Optional[str] = Field(default=None, description="Code snippet showing the problem, if applicable.")
    code_snippet_after: Optional[str] = Field(default=None, description="Code snippet showing the suggested refactoring, if applicable.")

# Main structured JSON schema for repository analysis
class CodebaseAnalysis(BaseModel):
    summary: str = Field(description="A concise, single-paragraph description of the project's core purpose and capabilities.")
    tech_stack: TechStack = Field(description="Categorized technical stack of the project.")
    features: List[str] = Field(default_factory=list, description="List of key features or capabilities implemented in the codebase.")
    setup_instructions: str = Field(description="Markdown formatted step-by-step installation, environment setup, and launch guide.")
    architecture: str = Field(description="Markdown formatted description of the system architecture, folder layout logic, and module interactions.")
    code_quality_reviews: List[CodeQualityReview] = Field(default_factory=list, description="List of code quality issues or improvement findings.")

def build_tree_text(tree: List[dict]) -> str:
    """
    Formats the repository tree into a text list for the prompt.
    """
    lines = []
    for item in tree:
        path = item.get("path", "")
        type_ = item.get("type", "")
        size = item.get("size", "")
        size_str = f" ({size} bytes)" if size else ""
        lines.append(f"- {path} [{type_}]{size_str}")
    return "\n".join(lines)

def build_codebase_context(file_contents: Dict[str, str]) -> str:
    """
    Formats the file paths and contents into a block of text.
    """
    blocks = []
    for path, content in file_contents.items():
        blocks.append(
            f"FILE PATH: {path}\n"
            f"----------------------------------------\n"
            f"{content}\n"
            f"----------------------------------------"
        )
    return "\n\n".join(blocks)

async def analyze_codebase(
    owner: str, 
    repo: str, 
    metadata: dict, 
    tree: List[dict], 
    file_contents: Dict[str, str],
    gemini_api_key: Optional[str] = None
) -> dict:
    """
    Analyzes the codebase structure and contents using the Gemini API.
    Returns a structured dictionary matching the desired schema.
    """
    api_key = gemini_api_key or config.GEMINI_API_KEY
    if not api_key:
        raise ValueError(
            "Gemini API Key is missing. Please add a valid 'GEMINI_API_KEY' to your backend/.env file, or provide one in the UI advanced settings."
        )

    directory_tree_text = build_tree_text(tree)
    codebase_context = build_codebase_context(file_contents)

    prompt = f"""You are a senior software architect and expert code reviewer.
Analyze the following public GitHub repository:
Repository: {owner}/{repo}
Description: {metadata.get('description') or 'No description provided.'}
Stars: {metadata.get('stars')} | Forks: {metadata.get('forks')}

Here is the directory structure (excluding binary/vendor folders):
{directory_tree_text}

Here is the source code and configuration of key files:
{codebase_context}

Perform a comprehensive repository analysis and output a structured JSON object according to the schema.
Ensure your markdown blocks are clean, and code quality reviews include helpful suggestions.
"""

    client = genai.Client(api_key=api_key)
    try:
        response = await client.aio.models.generate_content(
            model="gemini-3.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CodebaseAnalysis,
            ),
        )
        if response.parsed:
            return response.parsed.model_dump()
        else:
            # Fallback if parsed is empty but text is returned
            return json.loads(response.text)
    except Exception as e:
        raise Exception(f"Gemini API Error: {str(e)}")

async def chat_with_codebase(
    owner: str,
    repo: str,
    tree: List[dict],
    file_contents: Dict[str, str],
    history: List[dict],
    new_message: str,
    gemini_api_key: Optional[str] = None
) -> str:
    """
    Handles user questions relative to the cached codebase contents.
    history is a list of dicts like [{"role": "user"|"model", "content": "..."}]
    Returns the assistant response text.
    """
    api_key = gemini_api_key or config.GEMINI_API_KEY
    if not api_key:
        raise ValueError(
            "Gemini API Key is missing. Please add a valid 'GEMINI_API_KEY' to your backend/.env file, or provide one in the UI advanced settings."
        )

    # Reconstruct the system context
    directory_tree_text = build_tree_text(tree)
    codebase_context = build_codebase_context(file_contents)

    system_instruction = f"""You are a specialized AI assistant that helps developers understand this specific GitHub repository: {owner}/{repo}.
Here is the directory structure of the repository:
{directory_tree_text}

Here is the source code of key files in the repository:
{codebase_context}

Your task:
- Answer the user's questions contextually, using the codebase structure and contents provided.
- If a question refers to files or folders visible in the structure but whose content was not loaded, explain that the file content isn't fully indexed but guide them based on standard patterns or directories.
- Provide clean, copyable code blocks when writing or refactoring code.
- Maintain a helpful, developer-friendly, and concise tone.
"""

    client = genai.Client(api_key=api_key)
    
    sdk_contents = []
    for msg in history:
        role = "user" if msg.get("role") == "user" else "model"
        sdk_contents.append(
            types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg.get("content", ""))]
            )
        )
        
    sdk_contents.append(
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=new_message)]
        )
    )

    try:
        response = await client.aio.models.generate_content(
            model="gemini-3.5-flash",
            contents=sdk_contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction
            )
        )
        return response.text
    except Exception as e:
        raise Exception(f"Gemini API Error: {str(e)}")
