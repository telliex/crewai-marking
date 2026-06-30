import pytest
from awkns_outreach.tools.tier_classifier import classify_tier


def test_tier1_nail():
    assert classify_tier("Nail Salon") == 1

def test_tier1_facial():
    assert classify_tier("Facial Studio") == 1

def test_tier1_pilates():
    assert classify_tier("PILATES STUDIO") == 1

def test_tier1_lash():
    assert classify_tier("Lash Extensions") == 1

def test_tier2_barber():
    assert classify_tier("Barbershop") == 2

def test_tier2_yoga():
    assert classify_tier("Yoga & Wellness") == 2

def test_tier2_trainer():
    assert classify_tier("Personal Trainer") == 2

def test_tier3_coffee():
    assert classify_tier("Coffee Shop") == 3

def test_tier3_boba():
    assert classify_tier("Boba Tea Shop") == 3

def test_tier3_dessert():
    assert classify_tier("Dessert Cafe") == 3

def test_default_unknown():
    assert classify_tier("Pet Grooming") == 2

def test_default_empty():
    assert classify_tier("") == 2

def test_case_insensitive():
    assert classify_tier("nail salon") == 1
    assert classify_tier("COFFEE") == 3
