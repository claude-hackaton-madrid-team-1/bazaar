# inject-model.awk — insert `model: MODEL` into a markdown file's YAML frontmatter.
#
# Used by sync-ai-docs.sh to stamp a per-stage model id into the OPENCODE copy of a
# command/agent ONLY. The .ai/ source and the .claude/.gemini copies never get it, so
# vendor-anchored tools (Claude=Anthropic, Codex=OpenAI, Gemini=Google) stay clean.
#
# If the file has frontmatter (line 1 == "---"), insert the key right after the opening
# fence. If it has none, synthesize a minimal frontmatter block at the top.
#
# Usage: awk -v MODEL="openrouter/openai/gpt-5.5" -f inject-model.awk FILE
NR == 1 {
	if ($0 == "---") {
		print "---"
		print "model: " MODEL
		next
	}
	print "---"
	print "model: " MODEL
	print "---"
	print ""
	print
	next
}
{ print }
