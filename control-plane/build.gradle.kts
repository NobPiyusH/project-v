plugins {
	java
	id("org.springframework.boot") version "4.1.2"
	id("io.spring.dependency-management") version "1.1.7"
}

group = "com.projectv"
version = "0.1.0-SNAPSHOT"

java {
	toolchain {
		languageVersion = JavaLanguageVersion.of(21)
	}
}

repositories {
	mavenCentral()
}

extra["springAiVersion"] = "2.0.2"
extra["temporalVersion"] = "1.40.0"

dependencies {
	implementation("org.springframework.boot:spring-boot-starter-actuator")
	implementation("org.springframework.boot:spring-boot-starter-flyway")
	implementation("org.springframework.boot:spring-boot-starter-jdbc")
	implementation("org.springframework.boot:spring-boot-starter-validation")
	implementation("org.springframework.boot:spring-boot-starter-webmvc")
	implementation("org.flywaydb:flyway-database-postgresql")
	implementation("org.springframework.ai:spring-ai-starter-model-google-genai")
	implementation("com.anthropic:anthropic-java:3.3.0") // Claude: script + shot plan only (owner-funded, capped)
	// Plain Temporal SDK, wired by hand in TemporalConfig: the Temporal Spring Boot starter targets older Boot lines.
	implementation("io.temporal:temporal-sdk:${property("temporalVersion")}")
	implementation("org.yaml:snakeyaml")
	implementation(platform("software.amazon.awssdk:bom:2.59.1"))
	implementation("software.amazon.awssdk:s3")
	runtimeOnly("org.postgresql:postgresql")

	testImplementation("org.springframework.boot:spring-boot-starter-webmvc-test")
	testImplementation("io.temporal:temporal-testing:${property("temporalVersion")}")
	// Real-Postgres tests (Testcontainers 2.x, version managed by Spring Boot); skipped when Docker is not running.
	testImplementation("org.testcontainers:testcontainers-postgresql")
	testImplementation("org.testcontainers:testcontainers-junit-jupiter")
	testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

dependencyManagement {
	imports {
		mavenBom("org.springframework.ai:spring-ai-bom:${property("springAiVersion")}")
	}
}

tasks.withType<Test> {
	useJUnitPlatform()
	// Same as the app: UTC (Windows reports India as the legacy "Asia/Calcutta", which PostgreSQL rejects).
	systemProperty("user.timezone", "UTC")
}

// Run from the repo root so config/ and .env resolve the same way for every module.
tasks.named<org.springframework.boot.gradle.tasks.run.BootRun>("bootRun") {
	workingDir = rootProject.projectDir
	jvmArgs("-Djava.net.preferIPv4Stack=true")
}

// Copy-paste detector (PMD CPD): `./gradlew :control-plane:cpd` lists blocks of 100+ duplicated tokens.
// Report only (does not fail the build); PMD downloads the first time the task runs. scripts/check.ps1 runs it.
val cpdTool: Configuration by configurations.creating
dependencies {
	cpdTool("net.sourceforge.pmd:pmd-cli:7.29.0")
	cpdTool("net.sourceforge.pmd:pmd-java:7.29.0")
}
tasks.register<JavaExec>("cpd") {
	group = "verification"
	description = "Finds copy-pasted Java code (PMD CPD)."
	classpath = cpdTool
	mainClass = "net.sourceforge.pmd.cli.PmdCli"
	args("cpd", "--minimum-tokens", "100", "--language", "java", "--dir", "src/main/java", "--format", "text", "--no-fail-on-violation")
}
