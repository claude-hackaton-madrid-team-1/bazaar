# extract.awk — split a command/agent markdown source into its YAML-frontmatter
# `description` and its body. Mirrors splitFrontmatter() from the old sync-ai-docs.ts.
#
# Usage:
#   awk -v what=desc -v sq="'" -f extract.awk FILE   # prints the description (1 line)
#   awk -v what=body            -f extract.awk FILE   # prints the trimmed body (no trailing \n)
#
# A file "has frontmatter" only when line 1 is exactly `---`; otherwise the whole file
# is the body and the description is empty (same rule the TS regex enforced).
BEGIN { state = "maybe"; desc = ""; body = "" }
{
	if (state == "maybe") {
		if (NR == 1 && $0 == "---") { state = "fm"; next }
		state = "body"   # no frontmatter — fall through and treat line 1 as body
	}
	if (state == "fm") {
		if ($0 == "---") { state = "body"; next }
		if ($0 ~ /^description:[ \t]*/) {
			line = $0
			sub(/^description:[ \t]*/, "", line)
			desc = line
		}
		next
	}
	# state == "body"
	body = body $0 "\n"
}
END {
	if (what == "desc") {
		# strip ONE pair of surrounding quotes (double, or single via the sq var)
		if (desc ~ /^".*"$/) { sub(/^"/, "", desc); sub(/"$/, "", desc) }
		else if (sq != "" && desc ~ ("^" sq ".*" sq "$")) {
			sub("^" sq, "", desc); sub(sq "$", "", desc)
		}
		print desc
	} else {
		# JS .trim(): drop leading/trailing whitespace incl. newlines, no trailing \n
		sub(/^[ \t\r\n]+/, "", body)
		sub(/[ \t\r\n]+$/, "", body)
		printf "%s", body
	}
}
