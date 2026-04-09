
import os
import re
import time
import logging
from urllib.parse import quote
from multiprocessing import Pool
from io import BytesIO

import click
import requests

import pillow_avif
from PIL import Image, UnidentifiedImageError

VERSION = "2.0.0"

# Base API endpoint — sort, username, NSFW level, media type, and cursor are appended at runtime
INITIAL_URL = "https://civitai.com/api/v1/images"
DEFAULT_WORKERS = 1

# Used to strip HTML tags from prompt text before saving to .txt files
TAG_REGEX = re.compile(r'<.*?>')


def load_config_callback(ctx, param, value):
    """
    Click callback for --config. Parses a config file into ctx.default_map so
    that file values act as defaults and can be overridden by CLI flags.

    Config file format:
        # lines starting with # are comments
        --flag-name value
        --boolean-flag          (no value = True)
    """
    if not value:
        return

    try:
        with open(value, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError as e:
        raise click.BadParameter(f"Cannot read config file: {e}", param=param)

    config = {}
    for line in lines:
        # Strip inline and full-line comments, then whitespace
        line = line.split("#")[0].strip()
        if not line:
            continue

        parts = line.split(None, 1)
        flag = parts[0] if parts[0].startswith("-") else f"--{parts[0]}"

        # Look up the real parameter name from Click's param list — necessary
        # when the flag name differs from the parameter name (e.g. --type → media_type)
        key = None
        for p in ctx.command.params:
            if hasattr(p, 'opts') and flag in p.opts:
                key = p.name
                break
        if key is None:
            key = flag.lstrip("-").replace("-", "_")

        if len(parts) == 1:
            # Boolean flag with no value — e.g. --nsfw-only
            val = True
        else:
            raw = parts[1].strip()
            # Coerce "true"/"false" strings to bools for flag options
            if raw.lower() == "true":
                val = True
            elif raw.lower() == "false":
                val = False
            else:
                val = raw

        config[key] = val

    ctx.default_map = ctx.default_map or {}
    ctx.default_map.update(config)


class FilterParams:
    """Holds all threshold values used to filter API results before downloading."""
    def __init__(self, min_width, min_height, min_like, min_dislike, min_comment, min_hearts, min_cry, min_laugh, metadata_required, nsfw_only):
        self.min_width = min_width or 0
        self.min_height = min_height or 0

        self.min_like = min_like or 0
        self.min_dislike = min_dislike or 0
        self.min_comment = min_comment or 0
        self.min_hearts = min_hearts or 0
        self.min_cry = min_cry or 0
        self.min_laugh = min_laugh or 0

        self.metadata_required = metadata_required or False

        self.nsfw_only = nsfw_only or False


def filter_items(items, downloaded, filter_params: FilterParams):
    """
    Filter a page of API items against already-downloaded URLs and all active
    FilterParams thresholds. Returns only items that pass every check.
    The downloaded set stores URLs with a trailing newline (as read from the log file).
    """
    return [
        item for item in items if
        item['url'] + "\n" not in downloaded and

        item['width'] >= filter_params.min_width and
        item['height'] >= filter_params.min_height and

        item['stats']['likeCount'] >= filter_params.min_like and
        item['stats']['dislikeCount'] >= filter_params.min_dislike and
        item['stats']['commentCount'] >= filter_params.min_comment and
        item['stats']['cryCount'] >= filter_params.min_cry and
        item['stats']['laughCount'] >= filter_params.min_laugh and
        item['stats']['heartCount'] >= filter_params.min_hearts and

        (item['meta'] is not None if filter_params.metadata_required else True) and

        (item['nsfw'] if filter_params.nsfw_only else True)
    ]


def has_prompt(item):
    """Return True if the item has a non-null meta field containing a prompt."""
    if item['meta'] is not None:
        return "prompt" in item['meta']

    return False


def load_keywords(value):
    """
    If value is a path to an existing file, read its contents and return them
    as a comma-separated string of keywords (one per line). Otherwise return
    the value as-is, treating it as a literal comma-separated keyword string.
    """
    if value and os.path.isfile(value):
        with open(value, "r", encoding="utf-8") as f:
            keywords = [kw.strip() for line in f for kw in line.split(",") if kw.strip()]
            return ",".join(keywords)
    return value


def contains_keywords(item, require_keywords):
    """
    Return True if the item's prompt contains at least one keyword from the
    comma-separated require_keywords string. Used to allow-list specific content.
    """
    if not has_prompt(item):
        return False

    if require_keywords != "":
        prompt_lower = item['meta']['prompt'].lower()
        for keyword in require_keywords.split(","):
            if str(keyword).strip().lower() in prompt_lower:
                return True

    return False


def should_ignore(item, ignore_keywords):
    """
    Return True if the item's prompt contains any keyword from the
    comma-separated ignore_keywords string. Used to block specific content.
    """
    if not has_prompt(item):
        return False

    if ignore_keywords != "":
        prompt_lower = item['meta']['prompt'].lower()
        for keyword in ignore_keywords.split(","):
            if str(keyword).strip().lower() in prompt_lower:
                return True

    return False


def download_file(url, identifier, filepath, extension, compress=False, avif=False):
    """
    Download a single file from url and write it to filepath.

    Raises requests.HTTPError immediately on non-2xx responses so the caller
    can log the failure without writing a corrupt/empty file to disk.

    All writes go to a .tmp file first; os.replace() makes the rename atomic
    once the write completes. The finally block ensures the .tmp is cleaned up
    if anything goes wrong mid-write.

    When compress or avif is set, Pillow processes the image first. Any Pillow
    error (corrupt data, unsupported format, etc.) falls back to writing the
    raw response bytes so non-image files (e.g. videos) are always saved as-is.
    """
    item_response = requests.get(url)
    item_response.raise_for_status()

    content_type   = item_response.headers.get('Content-Type', 'unknown')
    content_length = item_response.headers.get('Content-Length', 'unknown')
    actual_bytes   = len(item_response.content)

    # Determine the final output path based on format flags
    raw_path = os.path.join(filepath, f"{identifier}.{extension}")
    if avif:
        final_path = os.path.join(filepath, f"{identifier}.avif")
    elif compress:
        final_path = os.path.join(filepath, f"{identifier}.jpg")
    else:
        final_path = raw_path

    tmp_path = final_path + ".tmp"

    try:
        if compress or avif:
            try:
                image = Image.open(BytesIO(item_response.content))

                # AVIF and JPEG don't support transparency — convert first
                if image.mode in ['RGBA', 'P']:
                    image = image.convert('RGB')

                if avif:
                    image.save(tmp_path,
                        quality=70 if compress else 100,
                        lossless=False if compress else True
                    )
                else:
                    # Pillow requires the .jpg extension to infer JPEG format
                    image.save(tmp_path, optimize=True, quality=80)

                # Atomic rename — final file only appears once fully written
                os.replace(tmp_path, final_path)

            except Exception:
                # Not an image (e.g. a video) — save with the original extension
                raw_tmp = raw_path + ".tmp"
                try:
                    with open(raw_tmp, "wb") as file:
                        file.write(item_response.content)
                    os.replace(raw_tmp, raw_path)
                finally:
                    if os.path.exists(raw_tmp):
                        os.remove(raw_tmp)
        else:
            with open(tmp_path, "wb") as file:
                file.write(item_response.content)

            # Atomic rename — final file only appears once fully written
            os.replace(tmp_path, final_path)

    finally:
        # Clean up temp file if the Pillow write or rename failed
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    return content_type, content_length, actual_bytes


def download_item(item, output_path, compress, avif, segment_by_date, segment_by_rating, require_keywords, ignore_keywords, save_prompt_files=True):
    """
    Orchestrate downloading a single API item.

    Builds the output directory (applying date/rating segmentation if requested),
    checks keyword filters, calls download_file(), then optionally writes the
    prompt text alongside the file. The prompt file is intentionally written
    after a successful download so no orphaned .txt files are left behind on failure.

    Returns a result dict with keys: error, ignored, identifier, url.
    """
    identifier = item['id']

    url = item['url']

    # Extract file extension from the CDN URL (e.g. "jpg", "mp4")
    extension = re.search(r'\.([a-zA-Z0-9]+)$', url).group(1)

    filepath = os.path.join(output_path)

    if segment_by_date:
        # Organise into YYYY-MM-DD subdirectories based on upload date
        date = item['createdAt'].split("T")[0]
        filepath = os.path.join(filepath, date)

        if not os.path.exists(filepath):
            os.makedirs(filepath)

    if segment_by_rating:
        # Organise into subdirectories by CivitAI NSFW rating level (numeric)
        rating = item['nsfwLevel']
        filepath = os.path.join(filepath, f"{rating}")

        if not os.path.exists(filepath):
            os.makedirs(filepath)

    if has_prompt(item):
        # Apply keyword filters — only items with a readable prompt are checked
        if should_ignore(item, ignore_keywords):
            return {
                "error": None,
                "ignored": True,
                "identifier": identifier,
                "url": url,
            }

        if require_keywords != "" and not contains_keywords(item, require_keywords):
            return {
                "error": None,
                "ignored": True,
                "identifier": identifier,
                "url": url,
            }

    try:
        content_type, content_length, actual_bytes = download_file(
            url,
            identifier,
            filepath,
            extension,
            compress,
            avif
        )

        # CivitAI's original=true CDN path sometimes serves a JPEG thumbnail
        # instead of the actual video. When that happens, retry using the
        # image-b2 CDN URL which is where the real video lives.
        b2_fallback = False
        if item.get('type') == 'video' and content_type.startswith('image/'):
            uuid_match = re.search(
                r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',
                url
            )
            if uuid_match:
                b2_url = f"https://image-b2.civitai.com/file/civitai-media-cache/{uuid_match.group()}/original"
                content_type, content_length, actual_bytes = download_file(
                    b2_url, identifier, filepath, 'mp4', compress, avif
                )
                b2_fallback = True

    except Exception as e:
        return {
            "error": e,
            "ignored": False,
            "identifier": identifier,
            "url": url,
            "content_type": None,
            "content_length": None,
            "actual_bytes": None,
            "b2_fallback": False,
        }

    if save_prompt_files and has_prompt(item):
        # Write the prompt alongside the image only after a successful download
        meta_prompt = TAG_REGEX.sub('', item['meta']['prompt'])
        meta_filename = os.path.join(filepath, f"{identifier}.txt")

        with open(meta_filename, "w", encoding='utf-8') as meta_file:
            meta_file.write(meta_prompt)

    return {
        "error": None,
        "ignored": False,
        "identifier": identifier,
        "url": url,
        "content_type": content_type,
        "content_length": content_length,
        "actual_bytes": actual_bytes,
        "b2_fallback": b2_fallback,
    }


@click.command()
@click.option("--config", type=click.Path(exists=True), default=None, is_eager=True, expose_value=False, callback=load_config_callback, help="Path to a config file with CLI flags")
@click.option("-d", "--debug", default=False, help="Enable debug logging")
@click.option("-s", "--silent", default=False, help="Disable logging")
@click.option("-k", "--api-key", help="API key for Civitai", required=True)
@click.option("-u", "--username", default=None, help="CivitAI username to scrape")
@click.option("-o", "--output-path", default=".", help="Path to save the images")
@click.option("-z", "--compress", default=False, help="Compress images to reduce file size", is_flag=True)
@click.option("-w", "--workers", default=DEFAULT_WORKERS, help="Number of workers to use for downloading")
@click.option("-l", "--limit",  default=0, help="Maximum number of images to download")
@click.option("-c", "--cursor", help="Cursor to start downloading from")
@click.option("--min-width", default=0, help="Minimum width of the image")
@click.option("--min-height", default=0, help="Minimum height of the image")
@click.option("--min-like", default=0, help="Minimum number of likes")
@click.option("--min-dislike", default=0, help="Minimum number of dislikes")
@click.option("--min-comment", default=0, help="Minimum number of comments")
@click.option("--min-hearts", default=0, help="Minimum number of hearts")
@click.option("--min-cry", default=0, help="Minimum number of cry reactions")
@click.option("--min-laugh", default=0, help="Minimum number of laugh reactions")
@click.option("--require-metadata", default=False, help="Only download images with metadata")
@click.option("--require-keywords", default="", help="CSV of keywords to match the prompt and require")
@click.option("--ignore-keywords", default="", help="CSV of keywords to match the prompt and ignore")
@click.option("--nsfw", default=False, help="Include NSFW images")
@click.option("--nsfw-only", default=False, help="Only download NSFW images")
@click.option("--segment-by-date", default=False, help="Segment images into directories by date", is_flag=True)
@click.option("--segment-by-rating", default=False, help="Segment images into directories by rating", is_flag=True)
@click.option("--avif", is_flag=True, help="Save images in AVIF")
@click.option("--no-prompt-files", is_flag=True, help="Skip saving prompt text files alongside downloads")
@click.option("--type", "media_type", default=None, type=click.Choice(["image", "video"], case_sensitive=False), help="Only download this media type")
@click.option("--sort", default="Newest", type=click.Choice(["Newest", "Most Reactions", "Most Comments"], case_sensitive=True), help="Sort order for results")
def scrape(
        debug,
        silent,
        api_key,
        username,
        output_path,
        compress,
        limit,
        workers,
        cursor,
        min_width,
        min_height,
        min_like,
        min_dislike,
        min_comment,
        min_hearts,
        min_cry,
        min_laugh,
        require_metadata,
        require_keywords,
        ignore_keywords,
        nsfw,
        nsfw_only,
        segment_by_date,
        segment_by_rating,
        avif,
        no_prompt_files,
        media_type,
        sort
):
    """Download images from Civitai API."""

    if debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if silent:
        logging.getLogger().setLevel(logging.CRITICAL)

    # Resolve keyword args — accepts either a file path (one keyword per line)
    # or a literal comma-separated string
    require_keywords = load_keywords(require_keywords)
    ignore_keywords = load_keywords(ignore_keywords)

    headers = {"Authorization": f"Bearer {api_key}"}

    # Build the initial API URL from the base + optional filters
    api_endpoint = INITIAL_URL + f"?sort={quote(sort)}"
    api_endpoint += f"&username={username}" if username else ""

    # CivitAI uses nsfw=false/X/true — "X" means include all ratings
    if nsfw_only:
        api_endpoint += "&nsfw=true"
    elif nsfw:
        api_endpoint += "&nsfw=X"
    else:
        api_endpoint += "&nsfw=false"

    if media_type:
        api_endpoint += f"&type={media_type}"

    if cursor:
        api_endpoint += f"&cursor={cursor}"

    if not os.path.exists(output_path):
        os.makedirs(output_path)

    # Load previously downloaded URLs to skip re-downloading across runs
    downloaded_urls_path = os.path.join(output_path, "downloaded.log")
    downloaded_urls = set()

    if os.path.exists(downloaded_urls_path):
        with open(downloaded_urls_path) as log_file:
            downloaded_urls = set(log_file.readlines())

    next_cursor = cursor

    with open(downloaded_urls_path, "a") as log_file:
        next_url = api_endpoint
        total_saved = 0

        while next_url and (limit == 0 or total_saved < limit):
            success = False
            retry_count = 0

            # Retry loop — handles both HTTP errors and JSON decode failures
            while not success and retry_count < 3:
                response = requests.get(next_url, headers=headers)

                if not response.ok:
                    logging.error(f"API returned HTTP {response.status_code}: {response.text}")
                    retry_count += 1
                    logging.info(f"Retrying in 30 seconds... (Attempt {retry_count})")
                    time.sleep(30)
                    continue

                try:
                    response_json = response.json()

                    success = True

                except requests.JSONDecodeError as e:
                    logging.error(f"Failed to decode JSON response: {e}")
                    logging.debug(f"Response: {response.text}")

                    retry_count += 1

                    logging.info(
                        f"Retrying in 30 seconds... (Attempt {retry_count})"
                    )

                    time.sleep(30)

            if not success:
                logging.fatal(
                    f"Failed to retrieve JSON response after 3 attempts. Exiting..."
                )

                return

            # Advance the cursor; absence of nextPage means we've reached the end
            if 'metadata' in response_json and 'nextPage' in response_json['metadata']:
                if next_cursor:
                    logging.info(f"Dowloading images from '{next_cursor}' to '{
                        response_json['metadata']['nextCursor']}'"
                    )
                else:
                    logging.info(f"Dowloading images from the latest entry to '{
                        response_json['metadata']['nextCursor']}'"
                    )

                next_cursor = response_json['metadata']['nextCursor']
                next_url = response_json['metadata']['nextPage']
            else:
                next_url = None

            filters = FilterParams(
                min_width,
                min_height,
                min_like,
                min_dislike,
                min_comment,
                min_hearts,
                min_cry,
                min_laugh,
                require_metadata,
                nsfw_only
            )

            filtered_items = filter_items(
                response_json['items'],
                downloaded=downloaded_urls,
                filter_params=filters
            )

            # Trim to the remaining quota so we never dispatch more than needed
            if limit != 0:
                filtered_items = filtered_items[:limit - total_saved]

            # Dispatch this page's items across the worker pool in parallel
            with Pool(workers) as pool:
                results = pool.starmap(
                    download_item,
                    [
                        (
                            item,
                            output_path,
                            compress,
                            avif,
                            segment_by_date,
                            segment_by_rating,
                            require_keywords,
                            ignore_keywords,
                            not no_prompt_files
                        ) for item in filtered_items
                    ]
                )

                for result in results:
                    if result:
                        if result['ignored']:
                            logging.info(f"Ignored {result['identifier']}.")

                        elif result['error'] is None:
                            label = "b2 fallback" if result.get('b2_fallback') else "ok"
                            logging.info(
                                f"Downloaded {result['identifier']} "
                                f"[{label}, {result['content_type']}, "
                                f"declared={result['content_length']}b, "
                                f"actual={result['actual_bytes']}b]"
                            )

                            total_saved += 1

                            # Record URL so it's skipped on future runs
                            log_file.write(f"{result['url']}\n")

                            downloaded_urls.add(result['url'])

                        else:
                            logging.error(
                                f"Failed to download {
                                    result['identifier']}: {result['error']}"
                            )

                if limit != 0 and total_saved >= limit:
                    break

    logging.info(
        f"Downloaded and saved {total_saved} images/videos and metadata files."
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    scrape(auto_envvar_prefix='CIVITAI_SCRAPER')
