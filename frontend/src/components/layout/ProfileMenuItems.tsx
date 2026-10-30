import { Link } from 'react-router-dom'
import { DropdownMenuItem } from '@/components/ui/dropdown-menu'
import { isExternalNav, type NavItem } from '@/config/navigation'
import { cn } from '@/lib/utils'

export function ProfileDropdownItems({
  items,
  onNavigate,
}: {
  items: NavItem[]
  onNavigate: (href: string) => void
}) {
  return (
    <>
      {items.map((item) =>
        isExternalNav(item.href) ? (
          <DropdownMenuItem key={item.href} asChild>
            <a href={item.href} className="cursor-pointer">
              <item.icon className="h-4 w-4 mr-2" />
              {item.label}
            </a>
          </DropdownMenuItem>
        ) : (
          <DropdownMenuItem
            key={item.href}
            onSelect={() => onNavigate(item.href)}
            className="cursor-pointer"
          >
            <item.icon className="h-4 w-4 mr-2" />
            {item.label}
          </DropdownMenuItem>
        )
      )}
    </>
  )
}

export function ProfileSheetItems({
  items,
  isActive,
  onClick,
}: {
  items: NavItem[]
  isActive: (href: string) => boolean
  onClick: () => void
}) {
  return (
    <>
      {items.map((item) =>
        isExternalNav(item.href) ? (
          <a
            key={item.href}
            href={item.href}
            onClick={onClick}
            className="flex items-center gap-3 rounded-lg px-3 py-3 text-sm transition-colors min-h-[44px] touch-manipulation hover:bg-muted active:bg-muted"
          >
            <item.icon className="h-4 w-4" />
            {item.label}
          </a>
        ) : (
          <Link
            key={item.href}
            to={item.href}
            onClick={onClick}
            className={cn(
              'flex items-center gap-3 rounded-lg px-3 py-3 text-sm transition-colors min-h-[44px] touch-manipulation',
              isActive(item.href)
                ? 'bg-primary text-primary-foreground'
                : 'hover:bg-muted active:bg-muted'
            )}
          >
            <item.icon className="h-4 w-4" />
            {item.label}
          </Link>
        )
      )}
    </>
  )
}
