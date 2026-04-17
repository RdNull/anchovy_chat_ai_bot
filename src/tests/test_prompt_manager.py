import pytest

from src.prompt_manager import PromptManager


def test_prompt_manager_loading(tmp_path):
    # Setup temporary prompts directory
    prompts_dir = tmp_path / 'prompts'
    task1_dir = prompts_dir / 'task1'
    task1_dir.mkdir(parents=True)
    (task1_dir / 'v1.j2').write_text('Hello {{ name }}!')
    (task1_dir / 'v2.j2').write_text('Hi {{ name }}!')

    task2_dir = prompts_dir / 'task2'
    task2_dir.mkdir(parents=True)
    (task2_dir / 'v1.j2').write_text('Goodbye {{ name }}.')

    manager = PromptManager(prompts_dir=str(prompts_dir))

    # Test rendering
    assert manager.get_prompt('task1', 'v1', name='World') == 'Hello World!'
    assert manager.get_prompt('task1', 'v2', name='Alice') == 'Hi Alice!'
    assert manager.get_prompt('task2', 'v1', name='Bob') == 'Goodbye Bob.'


def test_prompt_manager_errors():
    manager = PromptManager()  # Uses default settings.PROMPTS_DIR

    with pytest.raises(ValueError) as excinfo:
        manager.get_prompt('non_existent_task', 'v1')

    assert "Task 'non_existent_task' not found" in str(excinfo.value)

    # Assuming 'character_setup' exists from ls -R
    with pytest.raises(ValueError) as excinfo:
        manager.get_prompt('character_setup', 'nope')

    assert "Version 'nope' not found for task 'character_setup'" in str(excinfo.value)


def test_character_setup_v1_rendering():
    manager = PromptManager()

    character_description = 'A grumpy old man who loves tea.'
    memory = 'Known for shouting at clouds.'

    rendered = manager.get_prompt(
        'character_setup',
        'v1',
        character_description=character_description,
        memory=memory
    )

    expected_part1 = 'Ты — развлекательный бот-персонаж в групповом чате Telegram.'
    expected_part2 = 'Описание твоего персонажа:\nA grumpy old man who loves tea.'
    expected_part3 = 'Память чата (факты, решения, темы):\nKnown for shouting at clouds.'

    assert expected_part1 in rendered
    assert expected_part2 in rendered
    assert expected_part3 in rendered

    # Test without memory
    rendered_no_memory = manager.get_prompt(
        'character_setup',
        'v1',
        character_description=character_description,
        memory=None
    )
    assert expected_part3 not in rendered_no_memory


def test_memory_v1_rendering():
    manager = PromptManager()

    # Just a smoke test for memory template rendering
    rendered = manager.get_prompt(
        'memory',
        'v1',
        new_messages='User: hello',
        current_memory='Memory content'
    )
    assert 'НОВЫЕ СООБЩЕНИЯ:\nUser: hello' in rendered
    assert 'ТЕКУЩАЯ ПАМЯТЬ:\nMemory content' in rendered
