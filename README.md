 # CCSE TUI Study Tool

The **CCSE (Conocimientos Constitucionales y Socioculturales de España)** test is the official exam administered by the Instituto Cervantes. It is a mandatory requirement for obtaining Spanish citizenship through residence or for Sephardic descendants originating from Spain.

## Project Overview

This project provides a Terminal User Interface (TUI) study tool for the CCSE 2026 exam cycle, covering historical content through the elections of June 2023. It includes a question bank based on the official 2026 material and an interactive quiz interface for self-testing.

## Prerequisites

The script is written in Python 3 and depends only on the standard library, so no extra packages are required.

```bash
python --version   # Should be Python 3.x
```

## Running the TUI

1. **Navigate to the project directory**

  ```bash
  cd /home/rigo/git/CCSE-TUI-Study-Tool
  ```

2. **Launch the program**

  ```bash
  python ccse_tui.py
  ```

The interface uses arrow keys for navigation, `Enter` to confirm an answer and `q` to quit.

## File layout

- `ccse_tui.py` – The main executable script that implements the TUI logic.
- `reference/ccse_2026_preguntas.txt` – Raw list of exam questions.
- `reference/ccse_2026_respuestas.txt` – Corresponding answers.

## Updating for Future Years

This tool is designed to work with historical CCSE content. When preparing material for future exams:

1. **Create new files**: Add question and answer files in the same format for each election cycle (e.g., `reference/ccse_2027_preguntas.txt`, `reference/ccse_2027_respuestas.txt`).

2. **Maintain consistency**: Follow the established two-file structure where each line represents a single question or answer respectively.

3. **Versioning**: Update the year in filenames to match the exam cycle, allowing multiple versions to coexist for reference purposes.

4. **Extensibility**: The tool can handle multiple question sets - simply add new .txt files in the same directory structure when new elections occur.

## Contributing

If you find a mistake in the data or want to extend the tool, open a pull request. For major changes, please discuss first.

## Benefits of Spaced Repetition

Spaced repetition is a learning technique based on the psychological spacing effect. It involves studying information at increasing intervals, which optimizes memory retention and improves long-term recall.

### Key Benefits

- **Improved Memory Retention**: Studies show that spaced repetition can improve retention rates by up to 300% compared to traditional study methods.

- **Efficiency in Learning**: The algorithm adjusts review times based on your performance, ensuring you spend more time on difficult concepts and less on those you've mastered.

- **Long-Term Knowledge Retention**: This method mimics how our memory works naturally, strengthening recall over time without overwhelming your brain.

- **Reduced Study Time**: While spaced repetition requires consistent reviewing, the total study time is typically less than cramming because you only review what you need to remember.

### Statistics in the TUI

The CCSE TUI Study Tool provides valuable statistics about your learning progress:

1. **Card Count**: Total number of flashcards in your deck.
2. **Review Count**: Number of times you've reviewed cards, showing consistency in using the tool.
3. **Correct Answers**: Percentage or count of correct answers given during reviews.
4. **Time Spent Studying**: Total time dedicated to studying with the TUI.
5. **Retention Rate**: Estimated percentage of material you're likely retaining based on your review performance.
6. **Streaks (if implemented)**: Consecutive days you've studied using the tool, motivating regular practice.

### How Spaced Repetition Works in This Tool

1. Initial Review: When you first add a card, it will be reviewed soon (e.g., after 24 hours).
2. Subsequent Reviews: Based on your performance (easy/hard/good), the review interval increases:
   - Easy: Longer intervals (days or weeks)
   - Good: Moderate intervals
   - Hard: Shorter intervals (hours)
3. Gradual Spacing: The tool automatically adjusts intervals to optimize forgetting and reinforcement.

### Tips for Best Results

- Study consistently, even if only for a few minutes daily.
- Review cards right before bed; sleep helps solidify memories.
- Focus on understanding the material rather than just memorizing it.
- Use a mix of flashcards for different types of knowledge (facts, concepts, examples).