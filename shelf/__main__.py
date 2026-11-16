"""Run the app without systemd, for development: python -m shelf."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("shelf.web:app", host="127.0.0.1", port=5000, reload=True)
