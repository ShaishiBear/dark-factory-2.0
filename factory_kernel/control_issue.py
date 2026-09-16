"""Recognise original App control records without forgiving edited programme issues.

Labels and visible markers alone are not provenance. GitHub's immutable App creator and
unedited issue body distinguish a stop created by the control path from a work item whose
programme binding was removed. This classification never grants execution or completion.
"""
from __future__ import annotations

import re


def original_owner_stop(github, row: dict, app_login: str) -> bool:
    body = row.get("body")
    if (row.get("title") != "Owner requested factory stop"
            or row.get("user", {}).get("login") != app_login
            or row.get("user", {}).get("type") != "Bot"
            or type(row.get("number")) is not int or row["number"] <= 0
            or not isinstance(body, str)):
        return False
    match = re.fullmatch(r"<!-- dark-factory-owner-stop:[a-f0-9]{32} -->\n\n(.{1,2000})\n", body, re.DOTALL)
    if not match or not match[1].strip() or "<!--" in match[1]:
        return False
    owner, name = github.repository.split("/", 1)
    query = """query($owner:String!,$name:String!,$number:Int!) {
      repository(owner:$owner,name:$name) { nameWithOwner
        issue(number:$number) { number title body lastEditedAt author { login __typename } }
      }
    }"""
    response = github.json(["api", "graphql", "-f", f"query={query}",
                            "-f", f"owner={owner}", "-f", f"name={name}",
                            "-F", f"number={row['number']}"])
    if not isinstance(response, dict) or response.get("errors"):
        return False
    data = response.get("data")
    if not isinstance(data, dict):
        return False
    repository = data.get("repository")
    if not isinstance(repository, dict) or repository.get("nameWithOwner") != github.repository:
        return False
    issue = repository.get("issue")
    return (isinstance(issue, dict) and "lastEditedAt" in issue and issue["lastEditedAt"] is None
            and issue.get("number") == row["number"] and issue.get("title") == row["title"]
            and issue.get("body") == body
            # REST adds [bot]; GraphQL's Bot.login returns the App slug.
            and issue.get("author") == {"login": app_login.removesuffix("[bot]"), "__typename": "Bot"})
