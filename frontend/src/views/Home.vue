<script setup lang="ts">
import HeroSection from "@/components/home/HeroSection.vue"
import FeatureSection from "@/components/home/FeatureSection.vue"
import TemplateShowcase from "@/components/home/TemplateShowcase.vue"
import WorkflowSection from "@/components/home/WorkflowSection.vue"
</script>
<template>
    <main class="flex-1 bg-[#fafafa]">
      <HeroSection />
      <FeatureSection />
      <TemplateShowcase />
      <WorkflowSection />
    </main>
    <footer class="bg-zinc-50 border-t border-zinc-200 px-4 py-6 sm:px-6 sm:py-8 text-center">
      <p class="text-sm text-zinc-500 mb-3 font-medium">
        ResumeFlow 简历流 · 你的经历，值得更好的表达。
      </p>
      <div class="text-xs text-zinc-400 flex flex-col sm:flex-row items-center justify-center gap-1 sm:gap-3">
        <span>&copy; 2026 ResumeFlow</span>
      </div>
    </footer>
</template>
