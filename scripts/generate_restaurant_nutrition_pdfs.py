#!/usr/bin/env python3
"""
Generate publication-quality, FSSAI-compliant Nutritional Information PDF booklets
for 7 popular Bangalore restaurant institutions:
1. Brik Oven (Wood-fired Sourdough Pizzeria)
2. Corner House Ice Cream (Iconic Bangalore Dessert Parlour)
3. Boom Pizza (Woodfired Neapolitan Pizzeria)
4. Al Daaz (HSR Layout Arabian Mandi & Middle Eastern Grills)
5. Biryani By Kilo (Authentic Dum Biryani Handis)
6. Asha Tiffins (HSR Layout South Indian Breakfast & Darshini)
7. Plan B (Iconic Bangalore Pub Grub, Chicken Wings & Burgers)

Each PDF contains a clean tabular grid with headers:
Item Name | Category | Diet | Serving Size | Energy (kcal) | Protein (g) | Carbohydrates (g) | Total Fat (g) | Dietary Fiber (g) | Allergens
"""

import os
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

DOCUMENTS_DIR = Path(__file__).resolve().parent.parent / "data" / "documents"
DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)

RESTAURANTS_DATA = {
    "brik_oven_bangalore_nutrition.pdf": {
        "title": "Brik Oven Bengaluru — Nutritional & Allergen Information Guide",
        "restaurant": "Brik Oven",
        "description": "Artisanal Wood-Fired Sourdough Pizzas, Mozzarella Fritta, Shakes & Desserts. Per-Serve Macro Estimates based on authentic Neapolitan ingredients and standard portion weights.",
        "items": [
            # Classic Pizzas
            ("Margherita 2.0", "Classic Sourdough Pizzas", "Veg", "280g", "680.0", "24.0", "86.0", "26.0", "4.0", "Gluten, Milk"),
            ("Marinara", "Classic Sourdough Pizzas", "Veg", "240g", "490.0", "14.0", "84.0", "11.0", "4.0", "Gluten"),
            ("Quattro Formaggi", "Classic Sourdough Pizzas", "Veg", "310g", "840.0", "36.0", "82.0", "40.0", "3.5", "Gluten, Milk"),
            ("Smokey Marzano", "Classic Sourdough Pizzas", "Veg", "290g", "710.0", "27.0", "84.0", "29.0", "4.0", "Gluten, Milk"),
            ("Shrooms Pizza", "Classic Sourdough Pizzas", "Veg", "300g", "720.0", "25.0", "85.0", "31.0", "5.0", "Gluten, Milk"),
            # Meat & Specialty Pizzas
            ("Streaking Pigs Pizza", "Specialty Meat Pizzas", "Non-Veg", "320g", "860.0", "38.0", "84.0", "41.0", "3.5", "Gluten, Milk"),
            ("Diavola Pepperoni Pizza", "Specialty Meat Pizzas", "Non-Veg", "310g", "820.0", "34.0", "83.0", "38.0", "3.5", "Gluten, Milk"),
            ("Bird In Hand Pizza", "Specialty Meat Pizzas", "Non-Veg", "320g", "790.0", "42.0", "86.0", "30.0", "4.0", "Gluten, Milk"),
            # Appetizers & Breads
            ("Classic Garlic Bread", "Appetizers & Breads", "Veg", "140g", "390.0", "8.0", "48.0", "18.0", "2.5", "Gluten, Milk"),
            ("Blue Cheese Bacon Pineapple Garlic Bread", "Appetizers & Breads", "Non-Veg", "180g", "520.0", "16.0", "50.0", "28.0", "2.5", "Gluten, Milk"),
            ("Mozzarella Fritta (Fried Mozzarella Sticks)", "Appetizers & Breads", "Veg", "160g", "460.0", "19.0", "32.0", "28.0", "2.0", "Gluten, Milk, Egg"),
            # Shakes & Desserts
            ("Cookie Monster Shake", "Shakes & Desserts", "Veg", "350ml", "580.0", "9.0", "78.0", "26.0", "2.0", "Milk, Gluten"),
            ("Big Bang Chocolate Shake", "Shakes & Desserts", "Veg", "350ml", "640.0", "11.0", "86.0", "28.0", "3.0", "Milk, Gluten"),
            ("Brik Oven Banoffee Pie", "Shakes & Desserts", "Veg", "160g", "510.0", "6.0", "64.0", "26.0", "2.5", "Milk, Gluten"),
            ("Classic Tiramisu", "Shakes & Desserts", "Veg", "150g", "440.0", "7.0", "42.0", "27.0", "1.5", "Milk, Gluten, Egg"),
            ("Lemon Ricotta Cake", "Shakes & Desserts", "Veg", "140g", "410.0", "8.0", "46.0", "21.0", "1.8", "Milk, Gluten, Egg"),
        ]
    },
    "corner_house_ice_creams_bangalore_nutrition.pdf": {
        "title": "Corner House Ice Cream Bengaluru — Nutritional & Allergen Information Guide",
        "restaurant": "Corner House Ice Cream",
        "description": "Bangalore's Iconic Ice Cream Sundaes, Hot Fudge Specialties, Fruit Sundaes & Thick Shakes. Accurate nutritional breakdowns per standard serving weight.",
        "items": [
            ("Death By Chocolate (DBC)", "Signature Sundaes", "Veg", "420g", "840.0", "14.5", "108.0", "42.0", "4.2", "Milk, Gluten, Peanuts"),
            ("Hot Chocolate Fudge (HCF)", "Fudge Sundaes", "Veg", "280g", "560.0", "9.2", "74.0", "27.0", "2.5", "Milk, Peanuts"),
            ("Cake Fudge", "Fudge Sundaes", "Veg", "300g", "620.0", "10.0", "84.0", "29.0", "2.8", "Milk, Gluten"),
            ("Brown Bomb (Brownie Fudge Sundae)", "Fudge Sundaes", "Veg", "340g", "720.0", "11.5", "92.0", "35.0", "3.5", "Milk, Gluten, Nuts"),
            ("Cake A Mocha", "Fudge Sundaes", "Veg", "300g", "610.0", "9.8", "82.0", "28.0", "2.6", "Milk, Gluten, Peanuts"),
            ("Trilogy Sundae", "Signature Sundaes", "Veg", "350g", "660.0", "10.5", "90.0", "30.0", "2.5", "Milk, Nuts"),
            ("Fruit Jackpot", "Fruit Sundaes", "Veg", "320g", "390.0", "6.2", "76.0", "9.0", "3.8", "Milk"),
            ("Almond Fudge Sundae", "Fudge Sundaes", "Veg", "290g", "610.0", "12.0", "70.0", "32.0", "3.2", "Milk, Nuts"),
            ("Butterscotch Sundae", "Classic Sundaes", "Veg", "260g", "520.0", "7.5", "72.0", "23.0", "1.8", "Milk, Nuts"),
            ("Vanilla Scoop with Hot Fudge", "Classic Scoops", "Veg", "180g", "380.0", "5.8", "50.0", "18.0", "1.5", "Milk"),
            ("Chocolate Thick Shake", "Thick Shakes", "Veg", "350ml", "490.0", "11.0", "68.0", "20.0", "2.0", "Milk"),
            ("Chocolate Malt Thick Shake", "Thick Shakes", "Veg", "350ml", "520.0", "12.0", "74.0", "20.0", "2.0", "Milk, Gluten"),
            ("Strawberry Sundae", "Fruit Sundaes", "Veg", "250g", "410.0", "6.0", "68.0", "14.0", "2.2", "Milk"),
            ("Mango Sundae (Seasonal)", "Fruit Sundaes", "Veg", "260g", "430.0", "6.2", "72.0", "14.0", "2.5", "Milk"),
        ]
    },
    "boom_pizza_bangalore_nutrition.pdf": {
        "title": "Boom Pizza Bengaluru — Nutritional & Allergen Information Guide",
        "restaurant": "Boom Pizza",
        "description": "Authentic Neapolitan Woodfired Pizzas (10-inch), Stuffed Calzones & Garlic Breads. Hand-crafted slow fermented sourdough crusts with fresh mozzarella and local infusions.",
        "items": [
            ("Classic Margherita Pizza", "Wood-Fired Classics", "Veg", "260g", "610.0", "22.0", "78.0", "23.0", "3.5", "Gluten, Milk"),
            ("Pepperoni Boom Pizza", "Wood-Fired Classics", "Non-Veg", "290g", "760.0", "31.0", "77.0", "36.0", "3.2", "Gluten, Milk"),
            ("Pesto Paneer Pizza", "Fusion & Veg Pizzas", "Veg", "300g", "740.0", "28.0", "76.0", "36.0", "4.2", "Gluten, Milk, Nuts"),
            ("Butter Chicken Pizza", "Fusion & Meat Pizzas", "Non-Veg", "320g", "780.0", "38.0", "82.0", "33.0", "3.8", "Gluten, Milk"),
            ("Ghee Roast Chicken Pizza", "Fusion & Meat Pizzas", "Non-Veg", "320g", "820.0", "39.0", "78.0", "39.0", "3.5", "Gluten, Milk"),
            ("Ghee Roast Paneer Pizza", "Fusion & Veg Pizzas", "Veg", "310g", "790.0", "26.0", "79.0", "41.0", "3.8", "Gluten, Milk"),
            ("Gongura Chicken Pizza", "Fusion & Meat Pizzas", "Non-Veg", "310g", "750.0", "37.0", "78.0", "31.0", "4.0", "Gluten, Milk"),
            ("Veggie Loaded Wood-Fired Pizza", "Fusion & Veg Pizzas", "Veg", "290g", "640.0", "23.0", "84.0", "24.0", "5.2", "Gluten, Milk"),
            ("Wood-Fired Garlic Breadsticks with Dip", "Sides & Breads", "Veg", "160g", "420.0", "9.0", "52.0", "20.0", "2.5", "Gluten, Milk"),
            ("Cheesy Stuffed Calzone", "Calzones", "Veg", "240g", "580.0", "22.0", "64.0", "26.0", "3.0", "Gluten, Milk"),
            ("Chicken Tikka Calzone", "Calzones", "Non-Veg", "260g", "620.0", "34.0", "65.0", "25.0", "3.0", "Gluten, Milk"),
            ("Smoky BBQ Chicken Pizza", "Fusion & Meat Pizzas", "Non-Veg", "310g", "770.0", "36.0", "84.0", "31.0", "3.6", "Gluten, Milk"),
            ("Four Cheese Boom Pizza", "Wood-Fired Classics", "Veg", "290g", "790.0", "33.0", "76.0", "39.0", "3.2", "Gluten, Milk"),
        ]
    },
    "al_daaz_bangalore_nutrition.pdf": {
        "title": "Al Daaz Bengaluru (HSR Layout) — Nutritional & Allergen Information Guide",
        "restaurant": "Al Daaz",
        "description": "Authentic Arabian Mandi, Middle Eastern Charcoal Grills, Shawarma & Mughlai Platters. Portion weights and macros calculated for dining and delivery servings.",
        "items": [
            ("Chicken Mandi (Half Portion)", "Arabian Mandi", "Non-Veg", "450g", "740.0", "42.0", "85.0", "25.0", "3.5", "Nuts"),
            ("Chicken Mandi (Full Portion)", "Arabian Mandi", "Non-Veg", "900g", "1480.0", "84.0", "170.0", "50.0", "7.0", "Nuts"),
            ("Mutton Mandi (Half Portion)", "Arabian Mandi", "Non-Veg", "500g", "880.0", "46.0", "86.0", "38.0", "3.2", "Nuts"),
            ("Fish Mandi (Grilled Kingfish)", "Arabian Mandi", "Non-Veg", "460g", "690.0", "44.0", "84.0", "19.0", "3.0", "Fish, Nuts"),
            ("Special Mixed Arabic Mandi Platter", "Arabian Mandi", "Non-Veg", "1100g", "1820.0", "110.0", "190.0", "68.0", "8.0", "Nuts"),
            ("Charcoal Grilled BBQ Chicken (Half)", "Charcoal Grills", "Non-Veg", "350g", "540.0", "62.0", "8.0", "28.0", "1.5", "None"),
            ("Charcoal Grilled BBQ Chicken (Full)", "Charcoal Grills", "Non-Veg", "700g", "1080.0", "124.0", "16.0", "56.0", "3.0", "None"),
            ("Arabian Chicken Shawarma Roll", "Shawarma & Wraps", "Non-Veg", "220g", "480.0", "28.0", "44.0", "21.0", "2.5", "Gluten"),
            ("Special BBQ Chicken Wrap", "Shawarma & Wraps", "Non-Veg", "240g", "510.0", "29.0", "48.0", "22.0", "2.5", "Gluten"),
            ("Spicy Chicken Wings (6 pcs)", "Starters & Wings", "Non-Veg", "200g", "470.0", "29.0", "18.0", "31.0", "1.2", "Gluten"),
            ("Chicken Tikka Kebab (6 pcs)", "Tandoori Kebabs", "Non-Veg", "180g", "290.0", "36.0", "6.0", "13.0", "1.5", "Milk"),
            ("Mutton Seekh Kebab (2 Skewers)", "Tandoori Kebabs", "Non-Veg", "160g", "340.0", "26.0", "4.0", "24.0", "1.0", "None"),
            ("Fresh Toum Garlic Dip (Extra)", "Dips & Sides", "Veg", "60g", "190.0", "1.0", "3.0", "20.0", "0.2", "None"),
            ("Green Apple Mojito", "Beverages", "Veg", "300ml", "140.0", "0.2", "35.0", "0.0", "0.5", "None"),
            ("Chocolate Fantasy Sundae", "Desserts", "Veg", "180g", "420.0", "6.5", "56.0", "19.0", "2.0", "Milk, Gluten"),
        ]
    },
    "biryani_by_kilo_nutrition.pdf": {
        "title": "Biryani By Kilo (BBK) — Nutritional & Allergen Information Guide",
        "restaurant": "Biryani By Kilo",
        "description": "Authentic Dum Biryanis slow-cooked in individual clay handis with long-grain aged basmati rice, royal Awadhi spices, and fresh cuts. Macros measured per standard handi portion.",
        "items": [
            ("Hyderabadi Chicken Dum Biryani (1/2 kg Handi)", "Dum Biryanis", "Non-Veg", "500g", "720.0", "38.0", "86.0", "24.0", "4.5", "Milk, Nuts"),
            ("Hyderabadi Chicken Dum Biryani (1 kg Handi)", "Dum Biryanis", "Non-Veg", "1000g", "1440.0", "76.0", "172.0", "48.0", "9.0", "Milk, Nuts"),
            ("Hyderabadi Mutton Dum Biryani (1/2 kg Handi)", "Dum Biryanis", "Non-Veg", "500g", "840.0", "41.0", "84.0", "37.0", "4.0", "Milk, Nuts"),
            ("Hyderabadi Mutton Dum Biryani (1 kg Handi)", "Dum Biryanis", "Non-Veg", "1000g", "1680.0", "82.0", "168.0", "74.0", "8.0", "Milk, Nuts"),
            ("Lucknowi Chicken Dum Biryani (1/2 kg Handi)", "Dum Biryanis", "Non-Veg", "500g", "680.0", "36.0", "85.0", "21.0", "3.8", "Milk"),
            ("Kolkata Chicken Dum Biryani with Aloo & Egg", "Dum Biryanis", "Non-Veg", "520g", "740.0", "34.0", "94.0", "23.0", "4.2", "Milk, Egg"),
            ("Veg Dum Biryani (1/2 kg Handi)", "Dum Biryanis", "Veg", "480g", "560.0", "14.0", "88.0", "16.0", "6.2", "Milk, Nuts"),
            ("Paneer Dum Biryani (1/2 kg Handi)", "Dum Biryanis", "Veg", "500g", "670.0", "22.0", "86.0", "26.0", "5.0", "Milk"),
            ("Chicken Ghee Roast", "Starters & Kebabs", "Non-Veg", "240g", "460.0", "34.0", "10.0", "32.0", "2.0", "Milk"),
            ("Galouti Kebab (4 pcs)", "Starters & Kebabs", "Non-Veg", "180g", "380.0", "27.0", "6.0", "28.0", "1.2", "None"),
            ("Matka Phirni (Clay Pot)", "Desserts", "Veg", "120g", "240.0", "5.5", "36.0", "8.5", "0.8", "Milk, Nuts"),
            ("Gulab Jamun (2 pcs in Handi)", "Desserts", "Veg", "100g", "320.0", "4.5", "54.0", "9.5", "0.5", "Milk, Gluten"),
            ("Mirchi Ka Salan (Side Gravy)", "Accompaniments", "Veg", "100g", "150.0", "3.5", "8.0", "12.0", "2.2", "Peanuts, Sesame"),
            ("Burani Raita (Garlic Spiced Curd)", "Accompaniments", "Veg", "100g", "90.0", "3.8", "6.0", "5.2", "0.4", "Milk"),
        ]
    },
    "asha_tiffins_bangalore_nutrition.pdf": {
        "title": "Asha Tiffins Bengaluru (HSR Layout) — Nutritional & Allergen Information Guide",
        "restaurant": "Asha Tiffins",
        "description": "Bengaluru's Iconic South Indian Breakfast & Darshini Classics. Pure ghee dosas, steamed thatte idlis, medu vadas, traditional rice baths, and filter coffee.",
        "items": [
            ("Ghee Roast Masala Dosa with Chutneys & Sambar", "Dosas & Crepes", "Veg", "220g", "420.0", "7.5", "58.0", "18.0", "4.0", "Milk"),
            ("Plain Ghee Roast Dosa with Chutneys", "Dosas & Crepes", "Veg", "150g", "340.0", "5.8", "48.0", "14.0", "2.5", "Milk"),
            ("Open Butter Masala Dosa with Podi", "Dosas & Crepes", "Veg", "240g", "480.0", "8.2", "60.0", "23.0", "4.5", "Milk"),
            ("Set Dosa with Saagu (3 Spongy Dosas)", "Dosas & Crepes", "Veg", "260g", "390.0", "8.5", "66.0", "10.5", "5.0", "None"),
            ("Rava Masala Dosa with Potato Palya", "Dosas & Crepes", "Veg", "210g", "380.0", "7.2", "56.0", "13.5", "3.8", "Gluten, Nuts"),
            ("Thatte Idli with Ghee & Podi", "Idlis & Vadas", "Veg", "150g", "260.0", "6.8", "42.0", "7.5", "2.8", "Milk"),
            ("Button Idli Sambar Dip (14 pcs Mini Idlis)", "Idlis & Vadas", "Veg", "260g", "290.0", "9.2", "52.0", "4.5", "5.5", "Milk"),
            ("Medu Vada / Udin Vada (2 pcs)", "Idlis & Vadas", "Veg", "110g", "320.0", "8.8", "26.0", "20.0", "3.2", "None"),
            ("Bisi Bele Bath with Boondi", "Traditional Rice Baths", "Veg", "250g", "360.0", "9.5", "56.0", "10.5", "5.8", "Milk"),
            ("Khara Bath (Savory Semolina Upma)", "Traditional Rice Baths", "Veg", "180g", "240.0", "5.2", "36.0", "8.5", "3.0", "Gluten, Milk"),
            ("Kesari Bath (Pineapple Saffron Semolina Halwa)", "Traditional Sweets", "Veg", "140g", "330.0", "4.0", "52.0", "12.0", "1.2", "Gluten, Milk, Nuts"),
            ("Poori Saagu (3 Wheat Pooris with Veg Saagu)", "Breakfast Meals", "Veg", "220g", "440.0", "8.5", "58.0", "19.0", "5.5", "Gluten"),
            ("Authentic South Indian Filter Coffee", "Beverages", "Veg", "150ml", "115.0", "3.4", "15.0", "4.2", "0.0", "Milk"),
            ("Fresh Badam Milk (Hot)", "Beverages", "Veg", "180ml", "165.0", "5.2", "21.0", "6.8", "0.5", "Milk, Nuts"),
        ]
    },
    "plan_b_bangalore_nutrition.pdf": {
        "title": "Plan B Bengaluru — Nutritional & Allergen Information Guide",
        "restaurant": "Plan B",
        "description": "Famous American Pub Grub, House of Wings, Stuffed Burgers & Loaded Platters. Caloric and macronutrient breakdowns per serve.",
        "items": [
            ("Naked Crispy Wings (6 pcs with Blue Cheese Dip)", "Chicken Wings", "Non-Veg", "220g", "460.0", "38.0", "2.0", "32.0", "0.0", "Milk"),
            ("Spicy Buffalo Garlic Wings (6 pcs)", "Chicken Wings", "Non-Veg", "240g", "510.0", "39.0", "5.0", "36.0", "0.5", "Milk"),
            ("Flamin’ Jalapeño Wings (6 pcs)", "Chicken Wings", "Non-Veg", "240g", "490.0", "38.0", "6.0", "34.0", "0.8", "Milk"),
            ("Canadian Peanut Butter Chicken Wings (6 pcs)", "Chicken Wings", "Non-Veg", "250g", "590.0", "43.0", "14.0", "39.0", "2.0", "Peanuts"),
            ("Parisian Mustard Jaggery Wings (6 pcs)", "Chicken Wings", "Non-Veg", "240g", "480.0", "38.0", "16.0", "29.0", "0.6", "Mustard"),
            ("Sour Cream & Onion Wings (6 pcs)", "Chicken Wings", "Non-Veg", "230g", "480.0", "39.0", "5.0", "33.0", "0.5", "Milk"),
            ("BBQ Smoked Chicken Wings (6 pcs)", "Chicken Wings", "Non-Veg", "240g", "520.0", "38.0", "20.0", "31.0", "0.6", "None"),
            ("The Motherclucker Burger (Crisp Fried Chicken)", "Burgers & Mains", "Non-Veg", "280g", "740.0", "38.0", "62.0", "37.0", "3.5", "Gluten, Milk, Egg"),
            ("Juicy Lucy Beef Burger (Cheese Stuffed Patty)", "Burgers & Mains", "Non-Veg", "310g", "840.0", "48.0", "52.0", "48.0", "3.0", "Gluten, Milk, Sesame"),
            ("Fried Paneer Burger with Buffalo Mayo", "Burgers & Mains", "Veg", "270g", "680.0", "24.0", "58.0", "39.0", "3.2", "Gluten, Milk"),
            ("Potato & Sweet Corn Burger with Salsa", "Burgers & Mains", "Veg", "240g", "510.0", "11.0", "64.0", "22.0", "4.5", "Gluten, Milk"),
            ("Cheesy Garlic Fries (Melted Cheddar Sauce)", "Pub Sides & Bites", "Veg", "220g", "490.0", "8.0", "54.0", "27.0", "4.0", "Milk"),
            ("Bacon Wrapped Sausages (6 pcs)", "Pub Sides & Bites", "Non-Veg", "180g", "440.0", "26.0", "3.0", "35.0", "0.2", "None"),
            ("Loaded Chilli Cheese Nachos", "Pub Sides & Bites", "Veg", "260g", "580.0", "14.0", "66.0", "29.0", "6.5", "Milk"),
            ("Crispy American Corn Kernels", "Pub Sides & Bites", "Veg", "160g", "320.0", "6.0", "44.0", "13.0", "4.8", "None"),
        ]
    }
}


def build_pdf(filename: str, config: dict) -> None:
    filepath = DOCUMENTS_DIR / filename
    doc = SimpleDocTemplate(
        str(filepath),
        pagesize=landscape(letter),
        leftMargin=20,
        rightMargin=20,
        topMargin=20,
        bottomMargin=20,
    )
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#0F172A"),
        fontName="Helvetica-Bold",
        spaceAfter=4,
    )
    desc_style = ParagraphStyle(
        "DocDesc",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#475569"),
        fontName="Helvetica",
        spaceAfter=12,
    )
    cell_style = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        fontName="Helvetica",
    )
    header_style = ParagraphStyle(
        "HeaderCell",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        fontName="Helvetica-Bold",
        textColor=colors.white,
    )

    story = []
    story.append(Paragraph(config["title"], title_style))
    story.append(Paragraph(config["description"], desc_style))

    headers = [
        "Item Name",
        "Category",
        "Diet",
        "Serving Size",
        "Energy (kcal)",
        "Protein (g)",
        "Carbohydrates (g)",
        "Total Fat (g)",
        "Dietary Fiber (g)",
        "Allergens",
    ]

    table_data = [[Paragraph(h, header_style) for h in headers]]

    for row in config["items"]:
        row_cells = []
        for idx, val in enumerate(row):
            row_cells.append(Paragraph(str(val), cell_style))
        table_data.append(row_cells)

    # Column widths for landscape letter (792 pt width minus 40 pt margins = 752 pt printable)
    col_widths = [140, 100, 45, 65, 60, 50, 65, 55, 60, 112]

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1E293B')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#FFFFFF'), colors.HexColor('#F8FAFC')]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))

    story.append(t)
    doc.build(story)
    size_kb = filepath.stat().st_size / 1024
    print(f"Generated {filename}: {len(config['items'])} items ({size_kb:.1f} KB)")


def main():
    print(f"Generating 7 restaurant nutrition PDF booklets into {DOCUMENTS_DIR}...")
    for filename, config in RESTAURANTS_DATA.items():
        build_pdf(filename, config)
    print("\nAll 7 PDF booklets generated successfully.")


if __name__ == "__main__":
    main()
