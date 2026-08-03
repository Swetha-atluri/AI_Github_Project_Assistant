import re
import httpx
from typing import Dict, List, Tuple, Optional

# Regex to parse owner, repo, and optionally branch/path from a GitHub URL
# Matches:
# https://github.com/owner/repo
# https://github.com/owner/repo/tree/branch-name
# github.com/owner/repo.git
GITHUB_URL_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.)?github\.com/([^/]+)/([^/.]+)(?:/tree/([^/]+))?'
)

# File extensions/folders to skip during analysis
EXCLUDED_FOLDERS = {
    '.git', 'node_modules', 'venv', '.venv', '__pycache__', '.idea', 
    '.vscode', 'dist', 'build', 'out', 'target', 'bin', 'obj', 'cache'
}

EXCLUDED_EXTENSIONS = {
    # Archives
    '.zip', '.tar', '.gz', '.rar', '.7z', '.tgz',
    # Executables & binaries
    '.exe', '.dll', '.so', '.dylib', '.pyc', '.class', '.jar', '.war',
    # Media
    '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg', '.mp4', 
    '.mp3', '.wav', '.mov', '.avi', '.webp',
    # Fonts
    '.woff', '.woff2', '.ttf', '.eot', '.otf',
    # Databases & other binaries
    '.db', '.sqlite', '.sqlite3', '.parquet', '.pkl', '.bin', '.dat'
}

# Lock files that are text-based but typically verbose and low value for high-level logic analysis
EXCLUDED_LOCK_FILES = {
    'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock', 
    'Cargo.lock', 'go.sum', 'composer.lock', 'mix.lock'
}

def parse_github_url(url: str) -> Tuple[str, str, Optional[str]]:
    """
    Parses a GitHub URL to extract the owner, repository name, and optional branch.
    """
    url = url.strip()
    match = GITHUB_URL_PATTERN.search(url)
    if not match:
        raise ValueError("Invalid GitHub URL. Must be a public repository link.")
    
    owner = match.group(1)
    repo = match.group(2)
    branch = match.group(3)
    return owner, repo, branch

def get_headers(token: Optional[str] = None) -> Dict[str, str]:
    """
    Construct headers for GitHub API requests.
    """
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "AI-GitHub-Project-Assistant"
    }
    if token:
        headers["Authorization"] = f"token {token}"
    return headers

async def fetch_repo_metadata(owner: str, repo: str, token: Optional[str] = None) -> dict:
    """
    Fetches basic repository metadata from the GitHub REST API.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, headers=get_headers(token))
        if response.status_code == 404:
            raise ValueError(f"Repository '{owner}/{repo}' not found. Please verify the URL and that the repository is public.")
        elif response.status_code != 200:
            raise Exception(f"GitHub API Error: {response.status_code} - {response.text}")
        return response.json()

async def fetch_repo_tree(owner: str, repo: str, branch: str, token: Optional[str] = None) -> List[dict]:
    """
    Fetches the recursive Git tree for a specific branch.
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers=get_headers(token))
        if response.status_code != 200:
            raise Exception(f"Failed to fetch tree: {response.status_code} - {response.text}")
        data = response.json()
        return data.get("tree", [])

def filter_tree(tree: List[dict]) -> List[dict]:
    """
    Filters the tree list to remove folders and files that should not be parsed or analyzed.
    """
    filtered = []
    for item in tree:
        path = item.get("path", "")
        type_ = item.get("type", "")
        
        # Check if path contains any excluded folder
        parts = path.split('/')
        if any(part in EXCLUDED_FOLDERS for part in parts):
            continue
            
        if type_ == "blob":
            # Check extension
            has_excluded_ext = False
            for ext in EXCLUDED_EXTENSIONS:
                if path.lower().endswith(ext):
                    has_excluded_ext = True
                    break
            if has_excluded_ext:
                continue
                
            # Check lock files
            filename = parts[-1]
            if filename in EXCLUDED_LOCK_FILES:
                continue
                
        filtered.append(item)
    return filtered

async def fetch_raw_file_content(owner: str, repo: str, branch: str, path: str) -> str:
    """
    Fetches the raw text content of a file from raw.githubusercontent.com.
    """
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        if response.status_code == 200:
            return response.text
        else:
            # Fallback to empty string if file is not fetchable or is deleted/binary
            return ""

async def ingest_repository(
    url: str, 
    token: Optional[str] = None, 
    max_files: int = 50, 
    max_char_limit: int = 500000
) -> dict:
    """
    Parses, lists, filters and collects high-signal files from the repository.
    Returns:
        dict: {
            "metadata": repo_metadata,
            "tree": filtered_tree,
            "branch": branch,
            "file_contents": {path: contents}
        }
    """
    owner, repo, url_branch = parse_github_url(url)
    
    # 1. Fetch metadata to get default branch if not specified in URL
    metadata = await fetch_repo_metadata(owner, repo, token)
    branch = url_branch or metadata.get("default_branch", "main")
    
    # 2. Fetch full tree
    raw_tree = await fetch_repo_tree(owner, repo, branch, token)
    filtered = filter_tree(raw_tree)
    
    # 3. Separate configuration/meta files from regular source code files
    config_patterns = [
        r'package\.json$', r'requirements\.txt$', r'pyproject\.toml$', 
        r'cargo\.toml$', r'go\.mod$', r'setup\.py$', r'dockerfile$', 
        r'docker-compose\.yml$', r'readme\.md$', r'makefile$', r'\.env\.example$'
    ]
    
    config_files = []
    source_files = []
    
    for item in filtered:
        if item.get("type") == "blob":
            path = item.get("path", "")
            filename = path.split('/')[-1].lower()
            
            is_config = False
            for pattern in config_patterns:
                if re.search(pattern, filename):
                    is_config = True
                    break
            
            if is_config:
                config_files.append(item)
            else:
                source_files.append(item)
                
    # Sort files by size to avoid processing massive logs/data text files first
    config_files.sort(key=lambda x: x.get("size", 0))
    source_files.sort(key=lambda x: x.get("size", 0))
    
    # Prioritize loading config files first, then top source files
    files_to_load = config_files + source_files
    
    loaded_contents = {}
    current_char_count = 0
    loaded_count = 0
    
    for item in files_to_load:
        # Check limits
        if loaded_count >= max_files or current_char_count >= max_char_limit:
            break
            
        path = item.get("path")
        size = item.get("size", 0)
        
        # Don't download files that are excessively large individually
        if size > 150000: # 150KB
            continue
            
        content = await fetch_raw_file_content(owner, repo, branch, path)
        if content.strip():
            # Basic validation that it's text (not raw binary that slipped through)
            if '\x00' in content: # binary check
                continue
            loaded_contents[path] = content
            current_char_count += len(content)
            loaded_count += 1
            
    return {
        "owner": owner,
        "repo": repo,
        "branch": branch,
        "metadata": {
            "name": metadata.get("name"),
            "description": metadata.get("description"),
            "stars": metadata.get("stargazers_count"),
            "forks": metadata.get("forks_count"),
            "owner_avatar": metadata.get("owner", {}).get("avatar_url"),
            "html_url": metadata.get("html_url")
        },
        "tree": filtered,
        "file_contents": loaded_contents
    }
