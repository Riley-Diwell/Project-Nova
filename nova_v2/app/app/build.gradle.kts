import java.net.URI
import java.util.Properties
import java.util.zip.ZipInputStream

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.ksp)
}

val localProperties = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}
val novaApiKey: String = localProperties.getProperty("NOVA_API_KEY", "")

// --- Vosk offline STT model (see stt/VoskTranscriber.kt) --------------------
// Downloaded and unzipped at build time rather than committed to git - the
// same "bake the heavy ML asset in rather than fetch it at runtime" choice
// nova_v2/server's Dockerfile already makes for its fastembed model, just
// moved to build time since there's no Docker layer cache to do it in here.
// Cached under build/ once extracted (see app/.gitignore's /build), so this
// only actually hits the network on a clean build. Needs internet access the
// first time this module builds.
val voskModelUrl = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
val voskModelAssetName = "model-en-us" // must match VoskTranscriber.MODEL_ASSET_PATH
val voskGeneratedAssetsDir = layout.buildDirectory.dir("generated/voskAssets")
val voskModelDir = voskGeneratedAssetsDir.map { it.dir(voskModelAssetName) }
val voskModelZip = layout.buildDirectory.file("intermediates/vosk/model.zip")

val downloadVoskModel by tasks.registering {
    // Copied into plain local vals rather than read from voskModelUrl/voskModelZip/
    // voskModelDir directly inside doLast below - the configuration cache treats a
    // doLast lambda referencing a build-script top-level val as an (unserializable)
    // reference to the whole script object, even though the val itself is just a
    // String/Provider. Capturing local copies here, at configuration time, avoids that.
    val modelUrl = voskModelUrl
    val zipFileProvider = voskModelZip
    val outDirProvider = voskModelDir
    outputs.dir(outDirProvider)
    doLast {
        val outDir = outDirProvider.get().asFile
        // Already unpacked from a previous build - StorageService.unpack (see
        // VoskTranscriber) only needs to see this once per install, not once
        // per build.
        if (outDir.resolve("am/final.mdl").exists()) return@doLast

        val zipFile = zipFileProvider.get().asFile
        if (!zipFile.exists()) {
            zipFile.parentFile.mkdirs()
            logger.lifecycle("Downloading Vosk offline STT model from $modelUrl ...")
            URI(modelUrl).toURL().openStream().use { input ->
                zipFile.outputStream().use { output -> input.copyTo(output) }
            }
        }

        outDir.deleteRecursively()
        outDir.mkdirs()
        ZipInputStream(zipFile.inputStream()).use { zip ->
            var entry = zip.nextEntry
            while (entry != null) {
                // Strip the zip's own top-level "vosk-model-small-en-us-0.15/" folder -
                // StorageService.unpack expects the model's files directly under
                // assets/model-en-us/, not one directory deeper.
                val relative = entry.name.substringAfter('/', missingDelimiterValue = "")
                if (relative.isNotEmpty() && !entry.isDirectory) {
                    val outFile = outDir.resolve(relative)
                    outFile.parentFile.mkdirs()
                    outFile.outputStream().use { zip.copyTo(it) }
                }
                zip.closeEntry()
                entry = zip.nextEntry
            }
        }
        // StorageService.sync() requires this file, and only re-copies the model
        // to external storage when its contents change - bump it if this model
        // asset is ever swapped for a different one, so an in-place app update
        // doesn't keep serving a stale extraction from an old install.
        outDir.resolve("uuid").writeText("nova-vosk-model-en-us-0.15-v1")
    }
}

android {
    namespace = "com.example.novav2"
    compileSdk {
        version = release(36) {
            minorApiLevel = 1
        }
    }

    defaultConfig {
        applicationId = "com.example.novav2"
        minSdk = 24
        targetSdk = 36
        versionCode = 1
        versionName = "2.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        buildConfigField("String", "NOVA_API_KEY", "\"$novaApiKey\"")

        // vosk-android ships prebuilt native libs per ABI via JNA's .aar - restrict to
        // real-device ABIs rather than also packaging x86/x86_64 emulator variants.
        ndk.abiFilters.addAll(listOf("armeabi-v7a", "arm64-v8a"))
    }

    buildTypes {
        release {
            optimization {
                enable = false
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_11
        targetCompatibility = JavaVersion.VERSION_11
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    sourceSets {
        getByName("main") {
            // .get().asFile rather than passing the Provider directly - this AGP
            // version's classic SourceSet API rejects Provider<Directory> (it can't
            // tell a generated dir from a static one that way), and the path here
            // is static at configuration time regardless; preBuild.dependsOn(
            // downloadVoskModel) below is what actually guarantees it's populated
            // before anything reads from it.
            assets.srcDir(voskGeneratedAssetsDir.get().asFile)
        }
    }
}

tasks.named("preBuild") {
    dependsOn(downloadVoskModel)
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.compose.material.icons.extended)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.process)
    implementation(libs.androidx.room.runtime)
    implementation(libs.androidx.room.ktx)
    ksp(libs.androidx.room.compiler)
    implementation(libs.play.services.location)
    implementation(libs.okhttp)
    // Offline speech-to-text for the Nova device's BLE audio (see stt/VoskTranscriber.kt) -
    // Android's own SpeechRecognizer (used elsewhere in this app) can't accept a
    // pre-recorded buffer, only its own live mic, so BLE audio needs a separate
    // recognizer fed directly from decoded PCM. Coordinates/@aar match the upstream
    // vosk-android-demo's own build.gradle.
    implementation("net.java.dev.jna:jna:5.18.1@aar")
    implementation("com.alphacephei:vosk-android:0.3.75@aar")
    testImplementation(libs.junit)
    androidTestImplementation(platform(libs.androidx.compose.bom))
    androidTestImplementation(libs.androidx.compose.ui.test.junit4)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(libs.androidx.junit)
    debugImplementation(libs.androidx.compose.ui.test.manifest)
    debugImplementation(libs.androidx.compose.ui.tooling)
}
