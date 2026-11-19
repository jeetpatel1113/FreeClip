---
description: Clip a video with FreeClip for a Whop Content Rewards campaign, publish it, and submit the live URLs
argument-hint: <campaign_url> <video_path> [--auto]
allowed-tools: mcp__freeclip, mcp__playwright
---

# /clip-campaign

Arguments: `$ARGUMENTS`

Parse them as `<campaign_url> <video_path> [--auto]`:
- `campaign_url`: the first argument (a whop.com Content Rewards campaign link).
- `video_path`: the rest of the arguments up to an optional `--auto`. It may contain spaces or be quoted. It is a local video file, or a YouTube/Twitch/Kick URL.
- `AUTO`: true only if `--auto` is present.

If either argument is missing, stop and print the usage line.

Run the steps below in order. Stop at the first hard failure and give a short report: what happened, what was published, what was not submitted. Never invent campaign requirements, numbers or URLs. Use only what the page and the tools actually return.

## a) Read the campaign (Playwright MCP)

1. `browser_navigate` to `campaign_url`, then `browser_snapshot`. If the page is slow to load, use `browser_wait_for`.
2. **Login check.** If the page asks you to sign in, or shows a "Log in" / "Sign up" wall instead of the campaign, or you can't see campaign details, **stop and ask the user**:
   > "Please log in to Whop in the Playwright browser window that just opened, then tell me when you're done."

   Wait for their reply, then reload and snapshot again. Ask at most once. If you still can't see the campaign, abort.
3. Extract from the campaign page. Open any "Requirements", "Rules" or "Details" sections, and scroll if needed.
   - **Allowed platforms**: map them to `youtube`, `tiktok`, `instagram`, `x` and `linkedin`.
   - **Minimum clip length** in seconds, plus any maximum.
   - **Required hashtags, @mentions, disclosures** (for example `#ad`, "paid partnership", a brand tag), and any required caption text or links.
   - **Remaining budget** in USD. Also note the paid rate (for example "$X per 1K views").
   - **Content rules**: for example "must use the provided content", banned content, or language.
4. Print a compact table of these findings.
5. **Abort** if any of these is true:
   - The remaining budget is under **$500**, or can't be determined.
   - None of the allowed platforms is one FreeClip can post to.
   - The minimum length is over 60 s. FreeClip clips are 30–60 s.
   - The campaign requires specific source content and `video_path` clearly isn't it. Ask the user if it's unclear.
6. Build `caption_suffix`: the required hashtags, @mentions and disclosure text, space-separated, exactly as the campaign writes them, with no extras.

## b) Generate and publish (FreeClip MCP)

Call `run_freeclip_pipeline` with these arguments:
- `video_path`
- `caption_suffix`
- `platforms`: only the campaign's allowed platforms.
- `max_clips`: 1, unless the campaign explicitly allows multiple submissions and the user asked for more.
- `min_clip_seconds`: the campaign minimum, or 0.

This can take 5–20 minutes. If it returns `status: "still_processing"`, call it again with the returned `project_id` and the same other arguments. Repeat until it finishes or fails. Show any `warnings` to the user.

## c) Verify live URLs (FreeClip MCP)

Call `get_latest_published_urls` with a limit that covers the posts just created. For each post from this run, check:

- `status` is `PUBLISHED`.
- `submittable` is true.
- `url` is a real public post URL for its platform.
- `within_submission_window` is true. Content Rewards accepts posts up to 30 minutes old.

**TikTok.** If there's no URL, or the warning mentions Inbox/Drafts/SELF_ONLY/not public, **warn the user clearly**: the video was sent to TikTok Inbox/Drafts or posted privately, so it can't be submitted until it's posted publicly from the TikTok app. Skip it.

Skip every post that fails a check, and say why. If nothing is eligible, stop.

## d) Submit on Whop (Playwright MCP)

For each eligible post, one at a time, fastest-expiring first (lowest `minutes_left_in_window`):

1. Call `log_campaign_submission` with `campaign_url`, `platform`, `post_url` and `check_only=true`. If `duplicate` is true, skip this URL; never resubmit it.
2. On the campaign page, open the **Submit** dialog. The button may be labelled "Submit", "Submit content" or "Submit clip". Use `browser_snapshot` to find it.
3. Pick the matching platform if the dialog asks for one, then type or paste the post URL into the URL field. Fill any other required fields only with information you actually have. If a field needs information you don't have, ask the user.
4. **Before the final Submit click:**
   - If `AUTO` is false: `browser_take_screenshot` of the filled dialog, show the user the campaign, platform, URL and post age, then **stop and ask "Submit this URL? (yes/no)"**. Wait for an explicit yes. On "no", close the dialog and move to the next URL.
   - If `AUTO` is true: go ahead without asking. Every check above must still have passed.
5. Click the final Submit button. Then `browser_snapshot` and confirm Whop shows success: a confirmation message, or the submission appears as pending or under review.
   - If Whop shows an error (for example "already submitted", "link invalid" or "account not connected"), report the exact message, don't log the URL as submitted, and continue with the next URL.
6. Only after a confirmed success, call `log_campaign_submission` with `campaign_url`, `platform` and `post_url`. Leave `check_only` false.

## Final report

Print a short table with one row per post: platform, URL, published age, and submitted / skipped (with the reason) / failed. Then say:
- the FreeClip `project_id`
- that `published_clips.json` and `submitted_clips.csv` were updated
- which ready clips weren't used (from `unpublished_ready_clips`), in case the user wants to submit more later.
