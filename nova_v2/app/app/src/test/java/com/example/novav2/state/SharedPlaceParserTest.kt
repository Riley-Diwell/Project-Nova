package com.example.novav2.state

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SharedPlaceParserTest {
    @Test
    fun firstUrl_fromMapsShareText() {
        val text = "Birch Building\nBirch Bldg 35, Acton ACT 2601\nhttps://maps.app.goo.gl/Ab12Cd34"
        assertEquals("https://maps.app.goo.gl/Ab12Cd34", SharedPlaceParser.firstUrl(text))
        assertNull(SharedPlaceParser.firstUrl("just some words"))
    }

    @Test
    fun coordinates_prefersThePinOverTheViewport() {
        val url = "https://www.google.com/maps/place/Birch+Building/@-35.2700,149.1100,17z/" +
            "data=!3m1!4b1!4m6!3m5!1s0x6b164d!8m2!3d-35.2777!4d149.1185!16s%2Fg%2F11"
        assertEquals(Pair(-35.2777, 149.1185), SharedPlaceParser.coordinates(url))
    }

    @Test
    fun coordinates_fromViewportWhenNoPin() {
        val url = "https://www.google.com/maps/place/Somewhere/@-35.27,149.11,17z"
        assertEquals(Pair(-35.27, 149.11), SharedPlaceParser.coordinates(url))
    }

    @Test
    fun coordinates_fromQueryPairs() {
        assertEquals(Pair(-35.28, 149.13), SharedPlaceParser.coordinates("https://maps.google.com/?q=-35.28,149.13"))
        assertEquals(Pair(-35.28, 149.13), SharedPlaceParser.coordinates("https://maps.google.com/maps?ll=-35.28%2C149.13&z=15"))
        assertEquals(Pair(-35.28, 149.13), SharedPlaceParser.coordinates("geo:-35.28,149.13?q=Civic"))
    }

    @Test
    fun coordinates_noneForANamedQuery() {
        val url = "https://maps.google.com/maps?q=Birch+Building,+Acton+ACT&ftid=0x6b164d:0x1&entry=gps"
        assertNull(SharedPlaceParser.coordinates(url))
        assertEquals("Birch Building, Acton ACT", SharedPlaceParser.placeName(url))
    }

    @Test
    fun coordinates_rejectsOutOfRange() {
        assertNull(SharedPlaceParser.coordinates("https://maps.google.com/?q=135.0,149.13"))
    }

    @Test
    fun placeName_fromPlacePath() {
        assertEquals("Birch Building", SharedPlaceParser.placeName("https://www.google.com/maps/place/Birch+Building/data=!4m2"))
    }

    @Test
    fun labelAndSearchText_comeFromTheSharedWords() {
        val text = "Birch Building\nBirch Bldg 35, Acton ACT 2601\nhttps://maps.app.goo.gl/Ab12Cd34"
        assertEquals("Birch Building", SharedPlaceParser.label(text, null))
        assertEquals("Birch Building, Birch Bldg 35, Acton ACT 2601", SharedPlaceParser.searchText(text))
        assertNull(SharedPlaceParser.searchText("https://maps.app.goo.gl/Ab12Cd34"))
        assertEquals(
            "Civic",
            SharedPlaceParser.label("https://maps.app.goo.gl/x", "https://maps.google.com/?q=Civic"),
        )
    }

    @Test
    fun unwrap_consentPage() {
        val wrapped = "https://consent.google.com/ml?continue=https%3A%2F%2Fwww.google.com%2Fmaps%2F%40-35.2%2C149.1%2C17z&gl=AU"
        assertEquals("https://www.google.com/maps/@-35.2,149.1,17z", SharedPlaceParser.unwrap(wrapped))
        assertEquals("https://maps.google.com/?q=x", SharedPlaceParser.unwrap("https://maps.google.com/?q=x"))
    }
}
