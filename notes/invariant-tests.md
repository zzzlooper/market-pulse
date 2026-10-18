# Do the tests protect the CLAUDE.md invariants?

A mutation check, run on 2026-10-18 on branch `invariant-check` (from `62b7cba`). For each part of
each invariant: one small code change that breaks it, then the full suite (`python -m pytest`,
455 tests, Chromium present so the browser tests ran), then `git checkout -- <file>`. The suite
passed with no changes before starting. Nothing was fixed.

**Result: 45 of 58 breaks caught, 13 NOT CAUGHT.**

**Since then (branch `invariant-tests`):** each of the 13 has a test, and each break was run again
and now fails it. The tables below still show the run at `62b7cba`.

| # | Test that now catches it |
|---|---|
| 1i | `tests/test_resumptions.py` test_a_resumption_minutes_before_startup_isnt_reported_either |
| 1j | `tests/test_sympathy.py` test_a_stock_already_moving_when_its_own_industry_arrives_is_only_a_baseline |
| 1l | `tests/test_resumptions.py` test_a_resumption_under_a_minute_away_at_startup_still_gets_its_heads_up |
| 2b | `tests/test_alerts.py` test_cooldown_is_per_rule_and_symbol |
| 2m | `tests/test_halts.py` test_a_stock_halted_again_is_a_new_halt |
| 2n | `tests/test_resumptions.py` test_the_heads_up_comes_once_however_often_the_clock_is_checked |
| 2q | `tests/test_news.py` test_a_headline_trimmed_from_the_history_never_comes_back_as_new |
| 3a | `tests/test_finnhub.py` test_price_heard_recently_stays_live_without_the_stream (now uses 30 s, not `STALE_SECONDS`) |
| 4a | `tests/test_notify.py` test_per_minute_cap (now uses the real cap of 20) |
| 4e, 4f | `tests/test_browser.py` test_a_burst_of_alerts_makes_three_desktop_notifications_and_a_summary |
| 5c | `tests/test_notify.py` test_request_addresses_with_the_bot_token_stay_out_of_the_log |
| 5h | `tests/test_finnhub.py` test_an_error_message_from_finnhub_never_shows_the_key |

"Covering tests" are the tests that failed for a break. For a break that wasn't caught, the
nearest test is named along with why it missed.

## 1. A restart only sets a baseline; alerts never fire on startup

| # | Break | Failed tests |
|---|---|---|
| 1a | `alerts.py`: `crossed = hit and self._hit.get(key) is not True` (a rule's first reading counts as a crossing) | `tests/test_alerts.py:27` test_fires_on_crossing_not_on_first_reading; `tests/test_alerts.py:78` test_forget_rule_resets_baseline; `tests/test_tape.py:36` test_ssr_carried_over_for_a_mover_and_triggered_today_for_a_watchlist_stock |
| 1b | `alerts.py` `check_unusual`: the first list primes an empty set and falls through, so everything already listed fires | `tests/test_alerts.py:86` test_unusual_primes_then_fires_once_per_day; `tests/test_engine_alerts.py:48` test_unusual_alerts_wait_for_movers |
| 1c | `news.py`: `primed = True` (every feed treated as already fetched) | `tests/test_scenarios.py:126` test_a_feed_down_five_minutes_sends_one_outage_note_and_one_recovery |
| 1d | `filings.py`: `self._was_primed.pop(accession, True)` (every feed type treated as primed) | `tests/test_filings.py:281` test_a_document_that_fails_is_retried_and_a_backlog_filing_still_does_not_alert; `tests/test_scenarios.py:52` test_filings_already_out_at_startup_show_but_never_alert |
| 1e | `halts.py` `apply`: `if not self._primed:` → `if False:` | `tests/test_halts.py:46` test_feed_baseline_then_new_and_resumption; `tests/test_resumptions.py:75` test_a_resumption_before_startup_and_halts_off_the_watchlist_stay_quiet; `tests/test_scenarios.py:78` test_a_watchlist_halt_alerts_and_resumes_but_others_and_old_ones_dont |
| 1f | `breakouts.py`: a new stock starts as `_Stock(last=0.0)` and isn't returned early (its first reading can break the pre-market high) | `tests/test_breakouts.py:33` test_a_first_reading_after_the_open_is_only_a_baseline |
| 1g | `engine/risk.py` `_check_levels`: `if before is None: before = 0.0` instead of `continue` | `tests/test_warrants.py:219` test_crossing_a_warrant_price_alerts_once_a_day_and_the_first_price_is_only_a_baseline; `tests/test_warrants.py:275` test_a_stock_already_at_the_exercise_price_at_its_first_reading_hasnt_crossed_it; `tests/test_warrants.py:318` test_falling_through_an_offering_price_and_climbing_back_each_alert_once |
| 1h | `engine/tape.py` rotation: `(before or 0.0) < multiple` instead of `before is not None and before < multiple` | `tests/test_tape.py:64` test_trading_the_whole_float_alerts_once_and_the_first_reading_is_a_baseline |
| 1i | `engine/resumptions.py`: drop `hid not in self._resume_ahead or` (a resumption already past at startup is reported) | **NOT CAUGHT.** `tests/test_resumptions.py:75` has a resumption before startup (08:55), but the run starts at 09:31, more than `RESUME_GIVE_UP` (30 min) later, so the give-up branch drops it before the `_resume_ahead` check matters. No test starts within 30 min after a resumption. |
| 1j | `engine/tape.py` `_check_sympathy`: `{symbol, lead[0]}` → `{lead[0]}` (the stock's own first check is no longer a baseline) | **NOT CAUGHT.** `tests/test_sympathy.py:47` starts with the stock and its leader both new, so the leader's baseline hides the missing one. No test has a stock that is new while its leader was already seen. |
| 1k | Same line: `{symbol, lead[0]}` → `{symbol}` (the leader's first check is no longer a baseline) | `tests/test_sympathy.py:57` test_a_stock_already_moving_when_its_leaders_industry_arrives_is_only_a_baseline |
| 1l | Exception, `engine/resumptions.py`: the heads-up only fires for a halt seen on an earlier tick (none at startup, even when under 60 s away) | **NOT CAUGHT.** Every heads-up test (`tests/test_resumptions.py:56`, `:88`) sees the halt minutes before the warning window. No test starts the app less than `RESUME_WARN` before a resumption. |
| 1m | Exception, `luld.py` `Bands.update`: `near` is None on a stock's first price (a baseline for LULD) | `tests/test_sympathy.py:198` test_the_engine_uses_your_breakout_day2_and_luld_thresholds |

## 2. Each alert type has its own repeat rule

| # | Break | Failed tests |
|---|---|---|
| 2a | `alerts.py`: `COOLDOWN_SECONDS = 0` | `tests/test_alerts.py:47` test_cooldown_suppresses_flapping; `tests/test_scenarios.py:94` test_a_price_rule_fires_when_crossed_not_while_it_holds_and_respects_the_cooldown |
| 2b | `alerts.py`: cooldown keyed by rule only, `(rule["id"], "")`, not rule+symbol | **NOT CAUGHT.** The cooldown tests (`tests/test_alerts.py:47`, `tests/test_scenarios.py:94`) use one symbol. No test has an all-symbols rule (`symbol ""`) firing for two stocks within 30 min. |
| 2c | `breakouts.py`: drop `pullback >= min_pullback and` (new high without a pullback) | `tests/test_breakouts.py:24` test_the_pre_market_high_counts_after_the_open_once_and_from_the_last_pre_market_price; `tests/test_breakouts.py:33` test_a_first_reading_after_the_open_is_only_a_baseline; `tests/test_breakouts.py:38` test_a_new_high_of_day_counts_only_after_a_pullback; `tests/test_breakouts.py:86` test_a_gapper_breaks_its_pre_market_high_after_the_open; `tests/test_breakouts.py:105` test_a_gapper_on_no_list_still_breaks_its_pre_market_high_after_the_open |
| 2d | `breakouts.py`: `HOD_COOLDOWN = 0` | `tests/test_breakouts.py:46` test_new_highs_wait_out_the_cooldown_and_a_new_day_starts_over |
| 2e | `engine/tape.py` `_check_luld`: `< LULD_COOLDOWN` → `< 0` | `tests/test_luld.py:64` test_a_watchlist_stock_nearing_its_limit_up_band_is_warned_about_once |
| 2f | `engine/tape.py` `_check_luld`: cooldown keyed per stock `(symbol, "")`, not stock+side | `tests/test_luld.py:64` test_a_watchlist_stock_nearing_its_limit_up_band_is_warned_about_once |
| 2g | `breakouts.py`: drop `not s.pmh_done and` (pre-market high can fire again) | `tests/test_breakouts.py:24` test_the_pre_market_high_counts_after_the_open_once_and_from_the_last_pre_market_price; `tests/test_breakouts.py:86` test_a_gapper_breaks_its_pre_market_high_after_the_open; `tests/test_breakouts.py:105` test_a_gapper_on_no_list_still_breaks_its_pre_market_high_after_the_open |
| 2h | `alerts.py` `check_unusual`: `self._unusual_seen \|= symbols` → `pass` | `tests/test_alerts.py:86` test_unusual_primes_then_fires_once_per_day; `tests/test_alerts.py:98` test_unusual_disabled_still_tracks_seen |
| 2i | `engine/tape.py`: drop `and symbol not in self._rotation_fired` | `tests/test_tape.py:64` test_trading_the_whole_float_alerts_once_and_the_first_reading_is_a_baseline |
| 2j | `engine/tape.py`: drop `symbol not in self._sympathy_alerted and` | `tests/test_sympathy.py:31` test_a_stock_moving_with_its_industrys_leader_is_marked_and_alerted_once; `tests/test_sympathy.py:47` test_a_stock_already_moving_in_sympathy_at_startup_is_only_a_baseline; `tests/test_sympathy.py:57` test_a_stock_already_moving_when_its_leaders_industry_arrives_is_only_a_baseline; `tests/test_sympathy.py:95` test_a_pre_market_gapper_moving_with_its_industrys_leader_shows_in_the_plan |
| 2k | `engine/risk.py` `once()`: `return False` → `pass` (a level can fire again the same day) | `tests/test_warrants.py:219` test_crossing_a_warrant_price_alerts_once_a_day_and_the_first_price_is_only_a_baseline; `tests/test_warrants.py:318` test_falling_through_an_offering_price_and_climbing_back_each_alert_once |
| 2l | `halts.py` `apply`: drop `and not self._halts[hid]["resume_trade_at"]` (resumption time re-reported every poll) | `tests/test_halts.py:46` test_feed_baseline_then_new_and_resumption; `tests/test_resumptions.py:56` test_a_watchlist_halt_warns_a_minute_before_and_reports_how_it_resumed; `tests/test_resumptions.py:75` test_a_resumption_before_startup_and_halts_off_the_watchlist_stay_quiet; `tests/test_resumptions.py:88` test_with_halt_alerts_for_all_stocks_any_resumption_counts |
| 2m | `halts.py` `apply`: a new halt is skipped when its symbol is already in the list (a second halt of one stock is never reported) | **NOT CAUGHT.** `tests/test_halts.py:46` and `tests/test_scenarios.py:78` only use one halt per stock. No test has a stock halted twice in a day (common with repeated LULD pauses). |
| 2n | `engine/resumptions.py`: drop `and hid not in self._resume_warned` (heads-up can repeat) | **NOT CAUGHT.** `tests/test_resumptions.py:56` steps once a minute, so only one tick (10:04) lands in the 60 s window. No test ticks twice inside `RESUME_WARN` (the engine ticks every 5 s). |
| 2o | `engine/resumptions.py`: the halt isn't added to `_resume_done` after it's reported | `tests/test_resumptions.py:56` test_a_watchlist_halt_warns_a_minute_before_and_reports_how_it_resumed |
| 2p | `news.py`: `if item["id"] in self._seen and False:` (no dedupe) | `tests/test_backup_noise.py:71` test_news_and_filing_alerts_obey_snoozes_mutes_and_the_phone_cap; `tests/test_scenarios.py:126` test_a_feed_down_five_minutes_sends_one_outage_note_and_one_recovery |
| 2q | `news.py`: dedupe against `self._items` (trimmed to `news_history`) instead of `self._seen` | **NOT CAUGHT.** No test pushes more than `news_history` items and then sees an old one again. That is the case the `_seen` comment in `news.py` exists for. |
| 2r | `filings.py`: drop `accession not in self._seen` (no dedupe by accession number) | `tests/test_backup_noise.py:71` test_news_and_filing_alerts_obey_snoozes_mutes_and_the_phone_cap; `tests/test_filings.py:191` test_first_poll_is_a_baseline_and_later_filings_alert_for_watchlist_and_movers; `tests/test_filings.py:299` test_a_burst_bigger_than_a_page_is_read_from_the_next_page; `tests/test_scenarios.py:21` test_an_offering_on_a_top_gainer_alerts_once_with_its_risk_and_its_drop_reaches_the_recap; `tests/test_scenarios.py:52` test_filings_already_out_at_startup_show_but_never_alert |
| 2s | `news.py`: a cooldown-like rule added (only the first alerting item of a poll alerts) | `tests/test_backup_noise.py:71` test_news_and_filing_alerts_obey_snoozes_mutes_and_the_phone_cap |

## 3. Finnhub falls back to Yahoo's delayed price; Yahoo keeps its last good rows

| # | Break | Failed tests |
|---|---|---|
| 3a | `finnhub.py`: `STALE_SECONDS = 3600` | **NOT CAUGHT.** `tests/test_finnhub.py:72` and `:97` import `STALE_SECONDS` and time themselves against it, so they move along with the constant. Nothing pins the 30 s the invariant states. (The fallback itself is covered: 3b–3e.) |
| 3b | `finnhub.py` `overlay`: the fallback row isn't marked `delayed` | `tests/test_finnhub.py:72` test_stale_price_falls_back_to_yahoo_until_finnhub_recovers; `tests/test_finnhub.py:97` test_price_heard_recently_stays_live_without_the_stream |
| 3c | `finnhub.py` `handle`: a trade doesn't update `live.seen` (no switch back on a fresh trade) | `tests/test_finnhub.py:72` test_stale_price_falls_back_to_yahoo_until_finnhub_recovers |
| 3d | `finnhub.py` `refresh_quotes`: a REST quote doesn't update `live.seen` (no switch back on a REST quote) | `tests/test_finnhub.py:111` test_refresh_quotes_from_rest |
| 3e | `finnhub.py` `_fresh`: the "price since connect stays live while the stream is up" branch removed | `tests/test_finnhub.py:72` test_stale_price_falls_back_to_yahoo_until_finnhub_recovers |
| 3f | `engine/prices.py`: `if symbols and not rows:` → `if False:` (empty answer isn't an error, so health never goes stale) | `tests/test_engine_health.py:55` test_a_failed_yahoo_fetch_keeps_the_last_prices |
| 3g | `engine/prices.py`: an empty answer blanks `yahoo_quotes` before raising | `tests/test_engine_health.py:55` test_a_failed_yahoo_fetch_keeps_the_last_prices |
| 3h | `engine/prices.py`: a symbol missing from an answer loses its last row | `tests/test_engine_health.py:71` test_a_partial_yahoo_fetch_keeps_missing_rows_marked_stale |
| 3i | `engine/prices.py`: the kept row isn't marked `stale` | `tests/test_engine_health.py:71` test_a_partial_yahoo_fetch_keeps_missing_rows_marked_stale |

## 4. Notifications are rate-capped

| # | Break | Failed tests |
|---|---|---|
| 4a | `notify.py`: `PER_MINUTE = 10000` | **NOT CAUGHT.** `tests/test_notify.py:153` monkeypatches `PER_MINUTE` to 4, so the value 20 isn't pinned. (The cap mechanism is covered: 4g.) |
| 4g | `notify.py` `_allow`: the `len(self._sent) >= PER_MINUTE` check removed | `tests/test_notify.py:153` test_per_minute_cap |
| 4b | `notify.py` `run`: `outgoing = batch` (no burst batching/summary) | `tests/test_notify.py:133` test_run_filters_categories_and_collapses_bursts |
| 4c | `engine/core.py` `_route`: the `phone_cap_per_kind` condition removed | `tests/test_backup_noise.py:46` test_mute_snooze_and_the_phone_cap; `tests/test_backup_noise.py:71` test_news_and_filing_alerts_obey_snoozes_mutes_and_the_phone_cap |
| 4d | `engine/core.py` `_publish_items`: `to_phone = n["alert"]` after `_route` (news/filings skip the daily cap) | `tests/test_backup_noise.py:71` test_news_and_filing_alerts_obey_snoozes_mutes_and_the_phone_cap |
| 4e | `static/core.js`: `MAX_NOTIFICATIONS = 1000` | **NOT CAUGHT.** No test (browser or other) creates a desktop `Notification` or checks how many a batch makes. |
| 4f | `static/alerts.js` `notify`: loop over all `messages`, no `slice(0, MAX_NOTIFICATIONS)` | **NOT CAUGHT.** Same: desktop notifications are untested. |

## 5. Tokens and secrets never reach logs or output

| # | Break | Failed tests |
|---|---|---|
| 5a | `setup.py` `masked`: `return value` | `tests/test_browser.py:343` test_setup_saves_a_secret_but_never_shows_it_back; `tests/test_setup.py:63` test_masks_tell_values_apart_without_revealing_them; `tests/test_setup.py:87` test_secrets_go_in_but_never_come_back_out; `tests/test_setup.py:136` test_generated_values_are_shown_once_and_the_token_signs_this_browser_in; `tests/test_tls.py:160` test_a_browser_shows_the_encrypted_connection_and_how_to_install_the_ca |
| 5b | `backup.py` `exportable`: secrets count as exportable | `tests/test_backup_noise.py:11` test_a_backup_holds_no_secret_and_not_this_computers_address; `tests/test_backup_noise.py:25` test_restoring_checks_everything_and_leaves_secrets_out; `tests/test_browser.py:411` test_a_backup_downloads_without_secrets_and_restores_after_a_preview |
| 5j | `backup.py` restore: secrets in a backup file are accepted (`if False:` on the left-out branch) | `tests/test_backup_noise.py:25` test_restoring_checks_everything_and_leaves_secrets_out; `tests/test_browser.py:411` test_a_backup_downloads_without_secrets_and_restores_after_a_preview |
| 5c | `main.py`: `logging.getLogger("httpx").setLevel(logging.WARNING)` → `pass` | **NOT CAUGHT.** No test checks the httpx logger's level or that request URLs (Telegram bot token, ntfy topic) stay out of the log. |
| 5d | `engine/core.py`: the "settings changed" log line logs `name=value` | `tests/test_setup.py:87` test_secrets_go_in_but_never_come_back_out |
| 5e | `finnhub.py` `_redact`: `return text` | `tests/test_engine_health.py:101` test_finnhub_rest_failures_show_in_the_data_status; `tests/test_finnhub.py:183` test_stream_errors_never_contain_the_key; `tests/test_finnhub.py:202` test_stream_disconnect_logs_never_contain_the_key |
| 5f | `finnhub.py` `run`: `feed.error` on disconnect not redacted (log still is) | `tests/test_finnhub.py:183` test_stream_errors_never_contain_the_key |
| 5g | `finnhub.py` `run`: the disconnect log line not redacted (`feed.error` still is) | `tests/test_finnhub.py:202` test_stream_disconnect_logs_never_contain_the_key |
| 5h | `finnhub.py` `handle`: a Finnhub `{"type": "error"}` message logged and stored in `feed.error` unredacted | **NOT CAUGHT.** `tests/test_finnhub.py` only tests redaction on the disconnect path. No test sends an `error` message to `handle()`. |
| 5i | `news.py` `feed_error`: `text = str(exc)` (the full feed URL, maybe with a token, reaches the log) | `tests/test_news.py:35` test_feed_errors_never_repeat_the_address; `tests/test_news.py:46` test_a_failing_feed_with_a_token_in_its_address_keeps_it_out_of_the_log |

## Not caught, in short

- **1i**: a resumption that happened at most 30 min before startup gets reported.
- **1j**: sympathy at startup, when only the stock (not its leader) is new.
- **1l**: the resumption heads-up at startup when the resumption is under 60 s away (a documented exception to the baseline rule, untested).
- **2b**: the price-rule cooldown being per rule+symbol, not per rule.
- **2m**: a second halt of the same stock on the same day.
- **2n**: the heads-up firing only once per halt.
- **2q**: news dedupe outliving the trimmed history.
- **3a**: `STALE_SECONDS` = 30 (the mechanism is covered).
- **4a**: `PER_MINUTE` = 20 (the mechanism is covered by 4g).
- **4e, 4f**: desktop notifications collapsing to `MAX_NOTIFICATIONS`.
- **5c**: httpx request logging being silenced.
- **5h**: redaction of Finnhub `error` messages in `handle()`.
