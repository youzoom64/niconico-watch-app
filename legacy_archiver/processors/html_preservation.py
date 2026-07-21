"""既存HTMLを壊さず、明示した管理領域だけを更新する。"""

from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


PAGE_TAG_SCRIPT_ID = "archive-page-tags"
PAGE_TAG_BLOCK_START = "<!-- NICONICO-MANAGED:ARCHIVE-TAGS:START -->"
PAGE_TAG_BLOCK_END = "<!-- NICONICO-MANAGED:ARCHIVE-TAGS:END -->"


def normalize_tags(values: Iterable[Any] | str | None) -> list[str]:
    if isinstance(values, str):
        source: Iterable[Any] = [values]
    else:
        source = values or []
    result: list[str] = []
    for value in source:
        tag = str(value or "").strip()
        if tag and tag not in result:
            result.append(tag)
    return result


def safe_json(value: Any) -> str:
    """script要素を閉じる文字列を無害化したJSONを返す。"""
    return json.dumps(value, ensure_ascii=False).replace(
        "</", "<\\/"
    )


def atomic_write_text(path: Path | str, text: str) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, destination)


def _script_pattern(element_id: str) -> re.Pattern[str]:
    escaped = re.escape(element_id)
    return re.compile(
        rf"(<script\b(?=[^>]*\bid=[\"']{escaped}[\"'])[^>]*>)(.*?)(</script\s*>)",
        re.IGNORECASE | re.DOTALL,
    )


def replace_json_script(
    document: str,
    element_id: str,
    payload: Any,
) -> tuple[str, bool]:
    pattern = _script_pattern(element_id)
    match = pattern.search(document)
    if not match:
        raise ValueError(f"管理対象scriptがありません: {element_id}")
    replacement = f"{match.group(1)}{safe_json(payload)}{match.group(3)}"
    updated = document[: match.start()] + replacement + document[match.end() :]
    return updated, updated != document


def update_json_script_blocks(
    path: Path | str,
    payloads: dict[str, Any],
) -> bool:
    """既存HTMLのJSON scriptだけを更新する。HTML全体は再生成しない。"""
    html_path = Path(path)
    original = html_path.read_text(encoding="utf-8")
    updated = original
    for element_id, payload in payloads.items():
        updated, _changed = replace_json_script(updated, element_id, payload)
    if updated == original:
        return False
    atomic_write_text(html_path, updated)
    return True


def read_page_tags(document: str) -> list[str]:
    match = _script_pattern(PAGE_TAG_SCRIPT_ID).search(document)
    if not match:
        return []
    try:
        payload = json.loads(match.group(2).strip())
    except json.JSONDecodeError:
        return []
    return normalize_tags(payload if isinstance(payload, list) else [])


def read_page_tags_file(path: Path | str) -> list[str]:
    html_path = Path(path)
    if not html_path.is_file():
        return []
    try:
        return read_page_tags(html_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return []


def tag_page_filename(tag: str) -> str:
    safe_tag = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(tag or "").strip())
    safe_tag = safe_tag.rstrip(". ") or "untagged"
    return f"tag_{safe_tag}.html"


def annotate_person_occurrences(
    document: str,
    tags: Iterable[Any],
    aliases: dict[str, str] | None = None,
) -> tuple[str, dict[str, dict[str, Any]]]:
    match = re.search(
        r'(<script\b[^>]*id=["\']nico-virtual-timeline-data["\'][^>]*>)(.*?)(</script>)',
        document,
        re.IGNORECASE | re.DOTALL,
    )
    try:
        payload = json.loads(match.group(2)) if match else {}
    except (json.JSONDecodeError, AttributeError):
        payload = {}
    rows = list(payload.get("timeline1") or [])
    normalized = normalize_tags(tags)
    alias_map = aliases or {}
    name_to_tag: dict[str, str] = {}
    for tag in normalized:
        for name in [tag, *[alias for alias, canonical in alias_map.items() if canonical == tag and alias != tag]]:
            name_to_tag.setdefault(name.casefold(), tag)
    name_pattern = re.compile(
        "|".join(re.escape(name) for name in sorted(name_to_tag, key=len, reverse=True)),
        re.IGNORECASE,
    ) if name_to_tag else None
    stats: dict[str, dict[str, Any]] = {tag: {"count": 0, "seconds": []} for tag in normalized}
    unwrap = re.compile(
        r'<span class=["\']person-jump-target["\'][^>]*>(.*?)<small[^>]*>.*?</small>\s*</span>',
        re.IGNORECASE | re.DOTALL,
    )
    rows = [unwrap.sub(r'\1', str(row)) for row in rows]
    for index, row in enumerate(rows):
        plain = html.unescape(re.sub(r'<[^>]+>', '', row))
        for found in name_pattern.finditer(plain) if name_pattern else ():
            tag = name_to_tag[found.group(0).casefold()]
            stats[tag]["count"] += 1
            stats[tag]["seconds"].append(index * 10)
    ordinals = {tag: 0 for tag in normalized}

    def add_occurrence_count(match_obj):
        original = match_obj.group(0)
        canonical = name_to_tag[original.casefold()]
        ordinals[canonical] += 1
        ordinal = ordinals[canonical]
        total = stats[canonical]["count"]
        return (
            f'<span class="person-jump-target" data-person="{html.escape(canonical, quote=True)}" '
            f'data-occurrence="{ordinal}">{html.escape(original)}'
            f'<small> ({ordinal}/{total})</small></span>'
        )

    for index, row in enumerate(rows):
        parts = re.split(r'(<[^>]+>)', row)
        rows[index] = "".join(
            part if part.startswith("<") else name_pattern.sub(add_occurrence_count, part)
            for part in parts
        ) if name_pattern else row
    payload["timeline1"] = rows
    if match:
        serialized = safe_json(payload)
        document = document[:match.start()] + match.group(1) + serialized + match.group(3) + document[match.end():]
    return document, stats


def render_page_tag_block(
    tags: Iterable[Any],
    tag_page_prefix: str = "../tags/",
    stats: dict[str, dict[str, int]] | None = None,
) -> str:
    normalized = normalize_tags(tags)
    stats = stats or {}
    links = "".join(
        '<a class="archive-page-tag" href="#"'
        + ' data-person="' + html.escape(tag, quote=True)
        + '" data-seconds="' + ",".join(str(value) for value in (stats.get(tag) or {}).get("seconds", []))
        + '" style="display:inline-flex;padding:4px 10px;border-radius:999px;'
        'background:#2a2028;color:#f2c8d9;text-decoration:none;font-size:12px">#'
        + html.escape(tag)
        + ' <strong style="margin-left:5px">'
        + str(int((stats.get(tag) or {}).get("count") or 0))
        + '</strong>'
        + "</a>"
        for tag in normalized
    )
    return (
        f"{PAGE_TAG_BLOCK_START}\n"
        '<nav id="archive-page-tags-view" aria-label="タグ" '
        'style="display:flex;flex-wrap:wrap;gap:6px;padding:10px 14px;'
        'background:#17131a;border-bottom:1px solid #3a2b35;position:fixed;'
        'top:0;left:0;right:0;z-index:10000;margin:0">'
        f"{links}</nav>\n"
        f'<script id="{PAGE_TAG_SCRIPT_ID}" type="application/json">'
        f"{safe_json(normalized)}</script>\n"
        '<style>.person-jump-target{font-size:1.35em;font-weight:900;scroll-margin-top:80px}.person-jump-target small{font-size:.62em;color:#ff6fa8}'
        'html.archive-dark{filter:invert(1) hue-rotate(180deg);background:#fff}'
        'html.archive-dark img,html.archive-dark video,html.archive-dark canvas,html.archive-dark svg,html.archive-dark .emoji,html.archive-dark .emoji-no-invert{filter:invert(1) hue-rotate(180deg)!important}'
        'html.archive-dark a{filter:invert(1) hue-rotate(180deg)!important}'
        'html.archive-dark .ranking-item a,html.archive-dark .special-user-ranking-link{color:#72d7ff!important}'
        'html.archive-dark .ranking-item a:visited,html.archive-dark .special-user-ranking-link:visited{color:#d7a8ff!important}'
        'html.archive-dark a img,html.archive-dark a video,html.archive-dark a canvas,html.archive-dark a svg,html.archive-dark a .emoji,html.archive-dark a .emoji-no-invert{filter:none!important}'
        'html.archive-dark .ai-fullbody-backdrop img{filter:invert(1) hue-rotate(180deg) drop-shadow(0 22px 32px rgba(30,20,20,.20))!important}'
        'html.archive-dark #archive-page-tags-view{filter:invert(1) hue-rotate(180deg)!important}'
        'html.archive-dark #archive-page-tags-view a,html.archive-dark #archive-page-tags-view .emoji-no-invert{filter:none!important}'
        '.archive-dark-control{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}'
        '#archive-page-tags-view .archive-page-search{display:flex;align-items:center;gap:5px;margin-left:auto;min-width:300px}'
        '#archive-page-tags-view .archive-page-search input{width:min(260px,28vw);padding:5px 9px;border:1px solid #604253;border-radius:7px;background:#100d12;color:#fff;outline:none}'
        '#archive-page-tags-view .archive-page-search input:focus{border-color:#ff6fa8;box-shadow:0 0 0 2px rgba(255,111,168,.18)}'
        '#archive-page-tags-view .archive-page-search button{min-width:32px;padding:5px 8px;border:1px solid #604253;border-radius:7px;background:#2a2028;color:#f2c8d9;cursor:pointer}'
        '#archive-page-search-count{min-width:48px;text-align:center;color:#f2c8d9;font:12px monospace}'
        '@media(max-width:760px){#archive-page-tags-view .archive-page-search{width:100%;min-width:0;margin-left:0}#archive-page-tags-view .archive-page-search input{flex:1;width:auto}}</style>\n'
        '<script>(function(){const darkKey="niconicoArchiveDarkMode";function protectEmoji(root){const re=/[\\u{1F000}-\\u{1FAFF}\\u{2600}-\\u{27BF}]/u,walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT),nodes=[];while(walker.nextNode())if(re.test(walker.currentNode.nodeValue||"")&&!walker.currentNode.parentElement.closest("script,style,.emoji-no-invert"))nodes.push(walker.currentNode);for(const node of nodes){const parts=node.nodeValue.split(/([\\u{1F000}-\\u{1FAFF}\\u{2600}-\\u{27BF}](?:\\uFE0F|\\uFE0E)?)/u),frag=document.createDocumentFragment();for(const part of parts){if(!part)continue;if(re.test(part)){const span=document.createElement("span");span.className="emoji-no-invert";span.textContent=part;frag.appendChild(span)}else frag.appendChild(document.createTextNode(part))}node.replaceWith(frag)}}'
        'function initDarkMode(){const enabled=localStorage.getItem(darkKey)==="1";document.documentElement.classList.toggle("archive-dark",enabled);const controls=document.getElementById("controls-container");if(controls&&!document.getElementById("archiveDarkModeToggle")){const label=document.createElement("label");label.className="archive-dark-control";label.htmlFor="archiveDarkModeToggle";label.textContent="ダークモード:";const toggle=document.createElement("input");toggle.type="checkbox";toggle.id="archiveDarkModeToggle";toggle.checked=enabled;label.appendChild(toggle);controls.appendChild(label);toggle.addEventListener("change",()=>{localStorage.setItem(darkKey,toggle.checked?"1":"0");document.documentElement.classList.toggle("archive-dark",toggle.checked)})}protectEmoji(document.body)}'
        'function initPersonNav(){const nav=document.getElementById("archive-page-tags-view"),next={};'
        'if(!nav)return;const stats=document.querySelector(".stats");if(stats){nav.insertAdjacentElement("afterend",stats);stats.style.margin="-12px auto -2px";stats.style.maxWidth="1200px";stats.style.boxSizing="border-box";}'
        'const sync=()=>document.body.style.paddingTop=nav.offsetHeight+"px";sync();'
        'new ResizeObserver(sync).observe(nav);nav.addEventListener("click",e=>{const link=e.target.closest("[data-person]");'
        'if(!link)return;e.preventDefault();const person=link.dataset.person,seconds=(link.dataset.seconds||"").split(",").filter(Boolean).map(Number);'
        'if(!seconds.length)return;const index=next[person]||0,ordinal=index+1;next[person]=(index+1)%seconds.length;'
        'if(window.NicoVirtualTimeline)window.NicoVirtualTimeline.renderSecond(seconds[index],true);setTimeout(()=>{'
        'const target=document.querySelector(`.person-jump-target[data-person="${CSS.escape(person)}"][data-occurrence="${ordinal}"]`);'
        'if(target){document.querySelectorAll(".person-jump-target").forEach(x=>x.style.outline="");target.style.outline="3px solid #ff6fa8";'
        'target.scrollIntoView({behavior:"smooth",block:"center"});}},120);});}'
        'function initPageSearch(){const nav=document.getElementById("archive-page-tags-view");if(!nav||document.getElementById("archive-page-search-input"))return;'
        'const box=document.createElement("div");box.className="archive-page-search";box.innerHTML=`<input id="archive-page-search-input" type="search" placeholder="ページ内検索"><button type="button" data-search-dir="-1" title="前へ">▲</button><span id="archive-page-search-count">0 / 0</span><button type="button" data-search-dir="1" title="次へ">▼</button><button type="button" id="archive-page-search-clear" title="クリア">×</button>`;nav.appendChild(box);'
        'const input=box.querySelector("input"),count=box.querySelector("#archive-page-search-count");let ranges=[],index=-1;'
        'function collect(){ranges=[];index=-1;const query=input.value.trim().toLocaleLowerCase();if(!query){count.textContent="0 / 0";return}const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT,{acceptNode(node){const parent=node.parentElement;if(!parent||!node.nodeValue.trim()||parent.closest("#archive-page-tags-view,#controls-container,#timeline1,#timeline2,script,style,noscript"))return NodeFilter.FILTER_REJECT;return NodeFilter.FILTER_ACCEPT}});while(walker.nextNode()){const node=walker.currentNode,text=node.nodeValue.toLocaleLowerCase();let start=0;while((start=text.indexOf(query,start))!==-1){const range=document.createRange();range.setStart(node,start);range.setEnd(node,start+query.length);ranges.push({type:"dom",range});start+=query.length}}if(window.NicoVirtualTimeline?.search)for(const hit of window.NicoVirtualTimeline.search(query))ranges.push({type:"virtual",...hit});count.textContent=`0 / ${ranges.length}`} '
        'function selectVirtual(hit,query){const second=Number(hit.index||0)*10;window.NicoVirtualTimeline.renderIndex(hit.index,false);setTimeout(()=>{const block=document.querySelector(`#${hit.timelineId} .time-block[id="time_block_${second}"]`);if(!block)return;const walker=document.createTreeWalker(block,NodeFilter.SHOW_TEXT),needle=query.toLocaleLowerCase();let occurrence=0;while(walker.nextNode()){const node=walker.currentNode,text=(node.nodeValue||"").toLocaleLowerCase();let start=0;while((start=text.indexOf(needle,start))!==-1){if(occurrence===Number(hit.occurrence||0)){const range=document.createRange(),selection=getSelection();range.setStart(node,start);range.setEnd(node,start+needle.length);selection.removeAllRanges();selection.addRange(range);block.scrollIntoView({behavior:"smooth",block:"center"});return}occurrence+=1;start+=needle.length}}},80)}'
        'function jump(direction){if(!ranges.length)collect();if(!ranges.length)return;index=(index+direction+ranges.length)%ranges.length;const hit=ranges[index];if(hit.type==="virtual")selectVirtual(hit,input.value.trim());else{const range=hit.range,selection=getSelection();selection.removeAllRanges();selection.addRange(range);const target=range.startContainer.parentElement;if(target)target.scrollIntoView({behavior:"smooth",block:"center"})}count.textContent=`${index+1} / ${ranges.length}`} '
        'input.addEventListener("input",collect);input.addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();jump(event.shiftKey?-1:1)}});box.querySelectorAll("[data-search-dir]").forEach(button=>button.addEventListener("click",()=>jump(Number(button.dataset.searchDir))));box.querySelector("#archive-page-search-clear").addEventListener("click",()=>{input.value="";collect();getSelection().removeAllRanges();input.focus()});}'
        'const init=()=>{initDarkMode();initPersonNav();initPageSearch()};if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",init,{once:true});else init();})();</script>\n'
        f"{PAGE_TAG_BLOCK_END}"
    )


def upsert_page_tags(
    document: str,
    tags: Iterable[Any],
    tag_page_prefix: str = "../tags/",
    person_aliases: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """既存タグとの和集合だけを書き、タグを暗黙に削除しない。"""
    merged = normalize_tags([*read_page_tags(document), *normalize_tags(tags)])
    document, stats = annotate_person_occurrences(document, merged, person_aliases)
    block = render_page_tag_block(
        merged,
        tag_page_prefix,
        stats,
    )
    managed_pattern = re.compile(
        re.escape(PAGE_TAG_BLOCK_START)
        + r".*?"
        + re.escape(PAGE_TAG_BLOCK_END),
        re.DOTALL,
    )
    if managed_pattern.search(document):
        updated = managed_pattern.sub(lambda _match: block, document, count=1)
        return updated, merged

    body_match = re.search(r"<body\b[^>]*>", document, re.IGNORECASE)
    if not body_match:
        raise ValueError("タグを追加できるbody要素がありません")
    updated = document[: body_match.end()] + "\n" + block + document[body_match.end() :]
    return updated, merged


def update_page_tags_file(
    path: Path | str,
    tags: Iterable[Any],
    tag_page_prefix: str = "../tags/",
    person_aliases: dict[str, str] | None = None,
) -> tuple[bool, list[str]]:
    html_path = Path(path)
    original = html_path.read_text(encoding="utf-8")
    updated, merged = upsert_page_tags(original, tags, tag_page_prefix, person_aliases)
    if updated == original:
        return False, merged
    atomic_write_text(html_path, updated)
    return True, merged
