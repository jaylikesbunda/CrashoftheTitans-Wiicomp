    // NATIVE_SMALL_DISPATCH_EMITTER_V1: reproduce first-emitted switch cases.
    std::map<uint32_t, std::pair<std::string, bool>> choices;
    struct LookupRange { uint32_t start, end; std::string name; };
    std::vector<LookupRange> lookup_ranges;
    std::vector<uint32_t> lookup_ends;
    uint32_t high_end = 0;
    func_idx = 0;
    for (const auto &[start_addr, func] : analyzer_.get_functions()) {
      std::string name = all_func_names[func_idx];
      bool hle = !func.hle_hook_name.empty() || is_hle_function(all_base_names[func_idx]);
      ++func_idx;
      choices.emplace(start_addr, std::make_pair(hle && !func.hle_hook_name.empty() ? func.hle_hook_name : name, hle));
      if (hle) continue;
      lookup_ranges.push_back({start_addr, func.end_address, name});
      high_end = std::max(high_end, func.end_address);
      lookup_ends.push_back(high_end);
      for (const auto &inst : func.instructions) {
        ppc::Instruction instruction(inst.opcode);
        if (instruction.is_branch_link() || instruction.opcode() == 17)
          choices.emplace(inst.address + 4, std::make_pair(name, false));
        if (instruction.opcode() == 16 || instruction.opcode() == 18) {
          uint32_t target = instruction.branch_target(inst.address);
          bool local = std::find_if(func.instructions.begin(), func.instructions.end(),
            [target](const auto &i) { return i.address == target; }) != func.instructions.end();
          if (target <= inst.address && local)
            choices.emplace(inst.address, std::make_pair(name, false));
        }
      }
      for (uint32_t target : func.jump_table_targets)
        choices.emplace(target, std::make_pair(name, false));
    }
    out << "// NATIVE_SMALL_DISPATCH_V1\n";
    out << "struct DispatchChoice { uint32_t pc; void(*func)(CPUContext&); bool hle; };\n";
    out << "static const DispatchChoice g_dispatch_overrides[] = {\n";
    for (const auto &[pc, choice] : choices) {
      size_t i = std::upper_bound(lookup_ends.begin(), lookup_ends.end(), pc) - lookup_ends.begin();
      bool same = i < lookup_ranges.size() && lookup_ranges[i].start <= pc && lookup_ranges[i].name == choice.first;
      if (!same || choice.second)
        out << "    {0x" << std::hex << std::uppercase << pc << std::dec << ", " << choice.first << ", " << (choice.second ? "true" : "false") << "},\n";
    }
    out << "    {0, nullptr, false}\n};\n";
