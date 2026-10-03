package com.example.novav2.ui.components

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp

/**
 * One line of a note, as [NoteText] lays it out. Notes Nova writes use a little Markdown
 * (server memory_tool.py asks for '## ' headings and '- ' bullets); everything else is plain
 * text, and each of its lines stays a line - a poem keeps its shape.
 */
sealed interface NoteBlock {
    data class Heading(val level: Int, val text: String) : NoteBlock
    data class Bullet(val depth: Int, val text: String) : NoteBlock
    data class Numbered(val number: String, val depth: Int, val text: String) : NoteBlock
    data class Line(val text: String) : NoteBlock
    /** A blank line - a gap between sections or verses. Runs of them count once. */
    data object Gap : NoteBlock
}

private val HEADING = Regex("""^\s{0,3}(#{1,6})\s+(.*)$""")
private val BULLET = Regex("""^(\s*)[-*•]\s+(.*)$""")
private val NUMBERED = Regex("""^(\s*)(\d+)[.)]\s+(.*)$""")
private val BOLD = Regex("""\*\*(.+?)\*\*|__(.+?)__""")

/** Two spaces of indent per nesting level, as models and people write it. */
private fun depthOf(indent: String) = indent.replace("\t", "  ").length / 2

/** Pure, so it can be tested without Compose. */
fun parseNoteBlocks(text: String): List<NoteBlock> {
    val blocks = mutableListOf<NoteBlock>()
    for (raw in text.trim().lines()) {
        val line = raw.trimEnd()
        val heading = HEADING.matchEntire(line)
        val bullet = BULLET.matchEntire(line)
        val numbered = NUMBERED.matchEntire(line)
        when {
            line.isBlank() -> if (blocks.lastOrNull() != NoteBlock.Gap) blocks += NoteBlock.Gap
            heading != null ->
                blocks += NoteBlock.Heading(heading.groupValues[1].length, heading.groupValues[2].trim())
            bullet != null ->
                blocks += NoteBlock.Bullet(depthOf(bullet.groupValues[1]), bullet.groupValues[2].trim())
            numbered != null -> blocks += NoteBlock.Numbered(
                numbered.groupValues[2], depthOf(numbered.groupValues[1]), numbered.groupValues[3].trim(),
            )
            else -> blocks += NoteBlock.Line(line.trim())
        }
    }
    return blocks
}

/** **bold** and __bold__ inline; the markers themselves are dropped. */
fun inlineMarkup(text: String): AnnotatedString = buildAnnotatedString {
    var at = 0
    for (m in BOLD.findAll(text)) {
        append(text.substring(at, m.range.first))
        withStyle(SpanStyle(fontWeight = FontWeight.SemiBold)) {
            append(m.groupValues[1].ifEmpty { m.groupValues[2] })
        }
        at = m.range.last + 1
    }
    append(text.substring(at))
}

/** A note's text, laid out: headings, bullets, numbered lists, bold, and line breaks kept. */
@Composable
fun NoteText(text: String, modifier: Modifier = Modifier, style: TextStyle = MaterialTheme.typography.bodyLarge) {
    val blocks = remember(text) { parseNoteBlocks(text) }
    Column(modifier) {
        blocks.forEachIndexed { i, block ->
            when (block) {
                is NoteBlock.Heading -> Text(
                    inlineMarkup(block.text),
                    style = when (block.level) {
                        1 -> MaterialTheme.typography.titleLarge
                        2 -> MaterialTheme.typography.titleMedium
                        else -> MaterialTheme.typography.titleSmall
                    },
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.padding(top = if (i == 0) 0.dp else 8.dp, bottom = 2.dp),
                )
                is NoteBlock.Bullet -> ListItem("•", block.depth, block.text, style)
                is NoteBlock.Numbered -> ListItem("${block.number}.", block.depth, block.text, style)
                is NoteBlock.Line -> Text(inlineMarkup(block.text), style = style)
                NoteBlock.Gap -> Spacer(Modifier.height(10.dp))
            }
        }
    }
}

@Composable
private fun ListItem(marker: String, depth: Int, text: String, style: TextStyle) {
    Row(Modifier.padding(start = (16 * depth).dp, top = 2.dp, bottom = 2.dp)) {
        Text(marker, style = style, modifier = Modifier.width(22.dp))
        Text(inlineMarkup(text), style = style, modifier = Modifier.weight(1f))
    }
}
