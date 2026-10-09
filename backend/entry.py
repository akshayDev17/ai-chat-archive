"""Worker entrypoint.

Deliberately a shim, and deliberately at the project root rather than inside
the package.

`wrangler.jsonc` names the main module, and wrangler treats that file's
directory as the module root — flattening everything under it. Pointing `main`
at `chat_archive/entry.py` therefore made `chat_archive/` the root, dropped
`entry.py` at the top level with no parent package, and every `from .auth
import …` in it failed at boot:

    ImportError: attempted relative import with no known parent package

With the shim at the root, the root is `backend/`, and `chat_archive/` is
attached as an actual package, so its internal relative imports resolve. The
real entrypoint stays where it is, importable and testable.
"""

from chat_archive.entry import Default  # noqa: F401  (re-exported for the runtime)
