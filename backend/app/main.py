import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from app import github_service, ai_service, config

app = FastAPI(title="AI GitHub Project Assistant API")

# Configure CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory repository cache to hold file structure and loaded file contents
# Structure: {
#   "owner/repo": {
#       "owner": str,
#       "repo": str,
#       "branch": str,
#       "metadata": dict,
#       "tree": list,
#       "file_contents": dict, # {path: content}
#       "analysis": dict # AI analysis JSON
#   }
# }
REPO_CACHE: Dict[str, Dict[str, Any]] = {}

class AnalyzeRequest(BaseModel):
    url: str
    github_token: Optional[str] = None
    gemini_api_key: Optional[str] = None

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    repo_id: str
    messages: List[ChatMessage]
    user_message: str
    gemini_api_key: Optional[str] = None

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "gemini_configured": bool(config.GEMINI_API_KEY)}

@app.post("/api/analyze")
async def analyze_repo(req: AnalyzeRequest):
    print("Received Analyze Request:", req.model_dump())
    try:
        # Use custom user token if provided, otherwise fall back to backend .env config
        token = req.github_token or config.GITHUB_TOKEN or None
        
        # Ingest and filter files
        repo_data = await github_service.ingest_repository(req.url, token=token)
        
        owner = repo_data["owner"]
        repo = repo_data["repo"]
        repo_id = f"{owner}/{repo}".lower()
        
        # Run AI codebase analysis
        analysis_result = await ai_service.analyze_codebase(
            owner=owner,
            repo=repo,
            metadata=repo_data["metadata"],
            tree=repo_data["tree"],
            file_contents=repo_data["file_contents"],
            gemini_api_key=req.gemini_api_key
        )
        
        # Cache the codebase data and analysis result
        REPO_CACHE[repo_id] = {
            "owner": owner,
            "repo": repo,
            "branch": repo_data["branch"],
            "metadata": repo_data["metadata"],
            "tree": repo_data["tree"],
            "file_contents": repo_data["file_contents"],
            "analysis": analysis_result,
            "gemini_api_key": req.gemini_api_key
        }
        
        return {
            "repo_id": repo_id,
            "owner": owner,
            "repo": repo,
            "branch": repo_data["branch"],
            "metadata": repo_data["metadata"],
            "tree": repo_data["tree"],
            "analysis": analysis_result
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")

@app.post("/api/chat")
async def chat_repo(req: ChatRequest):
    repo_id = req.repo_id.lower()
    if repo_id not in REPO_CACHE:
        raise HTTPException(
            status_code=404, 
            detail="Repository not analyzed. Please run analysis first."
        )
        
    repo_data = REPO_CACHE[repo_id]
    
    try:
        history = [msg.model_dump() for msg in req.messages]
        # Prefer key passed in chat request, fallback to cached key
        api_key = req.gemini_api_key or repo_data.get("gemini_api_key")
        
        reply = await ai_service.chat_with_codebase(
            owner=repo_data["owner"],
            repo=repo_data["repo"],
            tree=repo_data["tree"],
            file_contents=repo_data["file_contents"],
            history=history,
            new_message=req.user_message,
            gemini_api_key=api_key
        )
        return {"reply": reply}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chat failed: {str(e)}")

@app.get("/api/file")
async def get_file_content(repo_id: str, path: str):
    key = repo_id.lower()
    if key not in REPO_CACHE:
        raise HTTPException(status_code=404, detail="Repository not found in cache.")
        
    repo_data = REPO_CACHE[key]
    
    # 1. Check if file is already loaded in cache
    if path in repo_data["file_contents"]:
        return {"content": repo_data["file_contents"][path]}
        
    # 2. Otherwise, fetch it dynamically from raw.githubusercontent.com
    try:
        content = await github_service.fetch_raw_file_content(
            owner=repo_data["owner"],
            repo=repo_data["repo"],
            branch=repo_data["branch"],
            path=path
        )
        # Store in cache to avoid refetching
        repo_data["file_contents"][path] = content
        return {"content": content}
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to fetch file content: {str(e)}"
        )

# Route static files
frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))
os.makedirs(frontend_dir, exist_ok=True)

# Mount the static directory
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

# Fallback route to serve index.html for SPA client-side routing
@app.exception_handler(404)
async def custom_404_handler(request, exc):
    index_file = os.path.join(frontend_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"detail": "Not Found"}
