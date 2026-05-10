import base64
import json
from typing import Any, Optional, Type

import requests
from crewai.tools import BaseTool
from pydantic import BaseModel, Field


REQUEST_TIMEOUT_SECONDS = 30
ERROR_PREVIEW_CHARS = 1000
FILE_PREVIEW_CHARS = 3000


class GitHubRepoSearchInputSchema(BaseModel):
    """Input schema for GitHub repository search."""

    query: str = Field(..., description="Search query, file path, or repository question.")
    content_type: str = Field("code", description="One of code, issue, pr, repo, or all.")
    limit: int = Field(5, ge=1, le=10, description="Maximum number of results per content type.")


class GitHubRepoSearchTool(BaseTool):
    """Small GitHub REST search tool that avoids embedding-provider dependencies."""

    name: str = "GitHub repository search"
    description: str = "Search a GitHub repository for code, issues, pull requests, and repository metadata using the GitHub REST API."
    args_schema: Type[BaseModel] = GitHubRepoSearchInputSchema
    github_repo: str
    gh_token: str = Field(exclude=True)
    content_types: list[str] = Field(default_factory=lambda: ["code", "repo", "pr", "issue"])
    api_base: str = "https://api.github.com"

    def __init__(self, github_repo: str, gh_token: str, content_types: Optional[list[str]] = None, **kwargs: Any) -> None:
        """Initialize the tool with repository scope and token."""
        super().__init__(
            github_repo=github_repo,
            gh_token=gh_token,
            content_types=content_types or ["code", "repo", "pr", "issue"],
            **kwargs,
        )
        self._generate_description()

    def _headers(self) -> dict[str, str]:
        """Build GitHub API request headers."""
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.gh_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _get_json(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Call a GitHub API endpoint and return a normalized JSON mapping."""
        response = requests.get(
            f"{self.api_base}{endpoint}",
            headers=self._headers(),
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            return {"status_code": response.status_code, "error": response.text[:ERROR_PREVIEW_CHARS]}
        payload = response.json()
        if isinstance(payload, dict):
            payload["status_code"] = response.status_code
            return payload
        return {"status_code": response.status_code, "items": payload}

    def _fetch_file_preview(self, api_url: str) -> Optional[str]:
        """Fetch a short text preview for a GitHub code-search result."""
        payload = self._get_json(api_url.replace(self.api_base, ""))
        if payload.get("status_code") != 200 or payload.get("encoding") != "base64":
            return None
        content = payload.get("content")
        if not isinstance(content, str):
            return None
        decoded = base64.b64decode(content).decode("utf-8", errors="replace")
        return decoded[:FILE_PREVIEW_CHARS]

    def _search_code(self, query: str, limit: int) -> dict[str, Any]:
        """Search repository code and include small file previews."""
        payload = self._get_json("/search/code", {"q": f"{query} repo:{self.github_repo}", "per_page": limit})
        items = []
        for item in payload.get("items", [])[:limit]:
            items.append(
                {
                    "name": item.get("name"),
                    "path": item.get("path"),
                    "html_url": item.get("html_url"),
                    "preview": self._fetch_file_preview(item.get("url", "")),
                }
            )
        return {"status_code": payload.get("status_code"), "total_count": payload.get("total_count"), "items": items}

    def _search_issues(self, query: str, limit: int, pull_requests: bool = False) -> dict[str, Any]:
        """Search repository issues or pull requests."""
        issue_type = "is:pr" if pull_requests else "is:issue"
        payload = self._get_json("/search/issues", {"q": f"{query} repo:{self.github_repo} {issue_type}", "per_page": limit})
        items = []
        for item in payload.get("items", [])[:limit]:
            items.append(
                {
                    "number": item.get("number"),
                    "title": item.get("title"),
                    "state": item.get("state"),
                    "html_url": item.get("html_url"),
                    "updated_at": item.get("updated_at"),
                }
            )
        return {"status_code": payload.get("status_code"), "total_count": payload.get("total_count"), "items": items}

    def _repo_metadata(self) -> dict[str, Any]:
        """Return basic repository metadata."""
        payload = self._get_json(f"/repos/{self.github_repo}")
        return {
            "status_code": payload.get("status_code"),
            "full_name": payload.get("full_name"),
            "default_branch": payload.get("default_branch"),
            "private": payload.get("private"),
            "html_url": payload.get("html_url"),
            "description": payload.get("description"),
            "updated_at": payload.get("updated_at"),
        }

    def _run(self, query: str, content_type: str = "code", limit: int = 5) -> str:
        """Run the requested GitHub repository search."""
        normalized_type = content_type.strip().lower()
        if normalized_type not in {"code", "issue", "pr", "repo", "all"}:
            normalized_type = "code"
        requested_types = self.content_types if normalized_type == "all" else [normalized_type]

        results: dict[str, Any] = {"github_repo": self.github_repo, "query": query, "results": {}}
        if "repo" in requested_types:
            results["results"]["repo"] = self._repo_metadata()
        if "code" in requested_types:
            results["results"]["code"] = self._search_code(query, limit)
        if "issue" in requested_types:
            results["results"]["issue"] = self._search_issues(query, limit, pull_requests=False)
        if "pr" in requested_types:
            results["results"]["pr"] = self._search_issues(query, limit, pull_requests=True)
        return json.dumps(results, indent=2)