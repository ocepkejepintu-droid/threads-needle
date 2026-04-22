"""Tests for anti-slop content validation rules."""

import pytest

from threads_analytics.content_rules import (
    validate_content,
    get_validation_feedback,
    _check_generic_opening,
    _check_has_specific_numbers,
    _check_no_corporate_speak,
    _check_not_advice_template,
    _check_has_emotional_hook,
)


class TestGenericOpeningRule:
    """Test generic opening detection."""
    
    def test_rejects_in_today_world(self):
        text = "In today's fast-paced world, businesses need to adapt"
        assert _check_generic_opening(text) is False
    
    def test_rejects_hey_everyone(self):
        text = "Hey everyone! I wanted to share something important"
        assert _check_generic_opening(text) is False
    
    def test_rejects_i_wanted_to_share(self):
        text = "I wanted to share my thoughts on hiring"
        assert _check_generic_opening(text) is False
    
    def test_accepts_direct_opener(self):
        text = "Susah juga ya cari akuntan yang bisa handle AR/AP/GL"
        assert _check_generic_opening(text) is True
    
    def test_accepts_story_opener(self):
        text = "Gue baru aja ngalamin hal paling nyebelin minggu ini"
        assert _check_generic_opening(text) is True


class TestSpecificNumbersRule:
    """Test specific numbers/details detection."""
    
    def test_accepts_dollar_amount(self):
        text = "We spent $5,000 on the new tool"
        assert _check_has_specific_numbers(text) is True
    
    def test_accepts_rupiah(self):
        text = "Biayanya Rp. 10 juta per bulan"
        assert _check_has_specific_numbers(text) is True
    
    def test_accepts_percentage(self):
        text = "Conversion rate naik 25% dalam seminggu"
        assert _check_has_specific_numbers(text) is True
    
    def test_accepts_time_duration(self):
        text = "Udah 3 jam gue coba fix ini"
        assert _check_has_specific_numbers(text) is True
    
    def test_accepts_year(self):
        text = "Since 2019 we've been growing"
        assert _check_has_specific_numbers(text) is True
    
    def test_rejects_vague_text(self):
        text = "Hiring good people is really important for business success"
        assert _check_has_specific_numbers(text) is False


class TestCorporateSpeakRule:
    """Test corporate buzzword detection."""
    
    def test_rejects_leverage(self):
        text = "We need to leverage our core competencies"
        assert _check_no_corporate_speak(text) is False
    
    def test_rejects_synergy(self):
        text = "Create synergy between teams"
        assert _check_no_corporate_speak(text) is False
    
    def test_rejects_optimize(self):
        text = "Optimize your workflow for better results"
        assert _check_no_corporate_speak(text) is False
    
    def test_rejects_scalable(self):
        text = "Make sure it's scalable for future growth"
        assert _check_no_corporate_speak(text) is False
    
    def test_accepts_casual_language(self):
        text = "Gue coba bikin sistem yang bisa dipake tim gue"
        assert _check_no_corporate_speak(text) is True


class TestAdviceTemplateRule:
    """Test advice template detection."""
    
    def test_rejects_here_are_tips(self):
        text = "Here are 5 tips for better hiring"
        assert _check_not_advice_template(text) is False
    
    def test_rejects_top_ways(self):
        text = "Top 10 ways to improve your business"
        assert _check_not_advice_template(text) is False
    
    def test_rejects_things_you_need(self):
        text = "10 things you need to know about AI"
        assert _check_not_advice_template(text) is False
    
    def test_rejects_ultimate_guide(self):
        text = "The ultimate guide to remote hiring"
        assert _check_not_advice_template(text) is False
    
    def test_accepts_personal_story(self):
        text = "Gue baru aja ngerekrut 3 orang minggu ini"
        assert _check_not_advice_template(text) is True


class TestEmotionalHookRule:
    """Test emotional hook detection."""
    
    def test_accepts_frustration(self):
        text = "Susah banget ya cari kandidat yang qualified!"
        assert _check_has_emotional_hook(text) is True
    
    def test_accepts_bingung(self):
        text = "Gue bingung nih, kenapa ya orang susah banget on time"
        assert _check_has_emotional_hook(text) is True
    
    def test_accepts_excitement(self):
        text = "Finally! Setelah 3 bulan, akhirnya kita close hiring ini"
        assert _check_has_emotional_hook(text) is True
    
    def test_accepts_question(self):
        text = "Ada yang pernah ngalamin hal serupa? Gimana solusinya?"
        assert _check_has_emotional_hook(text) is True
    
    def test_rejects_neutral_statement(self):
        text = "The process of hiring involves multiple steps"
        assert _check_has_emotional_hook(text) is False


class TestValidateContent:
    """Test full validation function."""
    
    def test_passes_good_content(self):
        # Example based on user's successful posts
        text = "Susah juga ya cari akuntan yang biasa AR/AP/GL bank reconcile (English book keeping), terbiasa komunikasi via Slack, cocok untuk US e-commerce startup. Gue udah 2 minggu searching, masih belum dapet yang pas!"
        result = validate_content(text)
        assert result.passed is True
        assert result.score >= 80
        assert len(result.failures) == 0
    
    def test_fails_generic_ai_content(self):
        text = "In today's fast-paced world, here are 5 tips for optimizing your hiring strategy. Leverage these best practices to scale your team effectively."
        result = validate_content(text)
        assert result.passed is False
        assert result.score < 60
        assert "no_generic_opening" in result.failures
        assert "has_specific_numbers" in result.failures
        assert "no_corporate_speak" in result.failures
        assert "not_advice_template" in result.failures
    
    def test_partial_pass_with_one_failure(self):
        # Has numbers and emotion, but corporate speak
        text = "Gue excited banget! Kita baru aja optimize hiring process dan naik 50%!"
        result = validate_content(text)
        # Should pass with warning (score 60-79, 1 failure)
        assert result.score >= 60
        assert len(result.failures) == 1
        assert result.failures[0] == "no_corporate_speak"
    
    def test_fails_short_content(self):
        text = "Hi"
        result = validate_content(text)
        assert result.passed is False
        assert result.score == 0
        assert "content_too_short" in result.failures
    
    def test_feedback_mapping(self):
        failures = ["no_generic_opening", "has_specific_numbers"]
        feedback = get_validation_feedback(failures)
        assert len(feedback) == 2
        assert "generic AI opening" in feedback[0]
        assert "Missing specific details" in feedback[1]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
